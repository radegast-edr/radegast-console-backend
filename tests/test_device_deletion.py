from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.associations import device_group_devices, exclusion_device_groups
from app.models.device import Device
from app.models.exclusion import Exclusion
from app.models.log import Log, LogSeen, LogSeverity
from app.models.user import User, UserRole
from app.services.device_deletion import delete_device_logs_and_device, process_deleted_devices
from app.services.space_usage import compute_all_space_usage


async def _get_default_team_and_group(client: AsyncClient) -> tuple[int, int]:
    resp = await client.get("/teams/")
    team_id = resp.json()[0]["id"]
    resp = await client.get(f"/teams/{team_id}/groups")
    group_id = resp.json()[0]["id"]
    return team_id, group_id


@pytest.mark.asyncio
class TestDeviceSoftDeletionAndHiding:
    async def test_soft_delete_hides_from_all_user_endpoints(
        self,
        auth_client: AsyncClient,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        team_id, group_id = await _get_default_team_and_group(auth_client)

        # 1. Create a device
        resp = await auth_client.post("/devices/", json={"name": "Device-SoftDel", "group_id": group_id})
        assert resp.status_code == 200
        device_data = resp.json()
        device_id = device_data["id"]
        token = device_data["token"]

        # Verify device login works before deletion
        device_client = AsyncClient(transport=auth_client._transport, base_url="http://test")
        resp = await device_client.post("/auth/device/login", json={"token": token})
        assert resp.status_code == 200

        # Verify device appears in list, detail, group, team, dashboard
        resp = await auth_client.get("/devices/")
        assert any(d["id"] == device_id for d in resp.json())

        resp = await auth_client.get(f"/devices/{device_id}")
        assert resp.status_code == 200

        resp = await auth_client.get(f"/groups/{group_id}")
        assert any(d["id"] == device_id for d in resp.json()["devices"])

        resp = await auth_client.get(f"/teams/{team_id}/devices")
        assert any(d["id"] == device_id for d in resp.json())

        resp = await auth_client.get("/dashboard/")
        assert any(d["id"] == device_id for d in resp.json()["devices"])

        # 2. Delete the device via user endpoint
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Device deleted"

        # 3. In DB, the device still exists with deleted_at set
        db_dev_res = await db_session.execute(select(Device).where(Device.id == device_id))
        db_dev = db_dev_res.scalar_one_or_none()
        assert db_dev is not None
        assert db_dev.deleted_at is not None

        # 4. Check that all API endpoints now treat it as nonexistent
        resp = await auth_client.get("/devices/")
        assert not any(d["id"] == device_id for d in resp.json())

        resp = await auth_client.get(f"/devices/{device_id}")
        assert resp.status_code == 404

        resp = await auth_client.patch(f"/devices/{device_id}", json={"name": "NewName"})
        assert resp.status_code == 404

        resp = await auth_client.post(f"/devices/{device_id}/reinstall")
        assert resp.status_code == 404

        resp = await auth_client.post(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 404

        resp = await auth_client.delete(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 404

        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 404

        # Group and Team endpoints exclude it
        resp = await auth_client.get(f"/groups/{group_id}")
        assert not any(d["id"] == device_id for d in resp.json()["devices"])

        resp = await auth_client.get(f"/teams/{team_id}/devices")
        assert not any(d["id"] == device_id for d in resp.json())

        resp = await auth_client.get("/dashboard/")
        assert not any(d["id"] == device_id for d in resp.json()["devices"])

        # Device authentication fails with 401
        device_client2 = AsyncClient(transport=auth_client._transport, base_url="http://test")
        resp = await device_client2.post("/auth/device/login", json={"token": token})
        assert resp.status_code == 401

    async def test_admin_endpoints_hide_soft_deleted_device(
        self,
        auth_client: AsyncClient,
        db_session: AsyncSession,
    ):
        _team_id, group_id = await _get_default_team_and_group(auth_client)

        # Make user admin
        resp = await auth_client.get("/user/me")
        user_id = resp.json()["id"]

        u_res = await db_session.execute(select(User).where(User.id == user_id))
        user = u_res.scalar_one()
        user.role = UserRole.admin
        await db_session.commit()

        # Create device
        resp = await auth_client.post("/devices/", json={"name": "AdminDelDevice", "group_id": group_id})
        device_id = resp.json()["id"]

        # Visible in admin devices
        resp = await auth_client.get("/admin/devices")
        assert resp.status_code == 200
        assert any(d["id"] == device_id for d in resp.json())

        # Admin delete device
        resp = await auth_client.delete(f"/admin/devices/{device_id}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Device deleted"

        # Not visible in admin devices
        resp = await auth_client.get("/admin/devices")
        assert resp.status_code == 200
        assert not any(d["id"] == device_id for d in resp.json())

        # Second admin delete returns 404
        resp = await auth_client.delete(f"/admin/devices/{device_id}")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceDeletionWorker:
    async def test_worker_deletes_device_and_logs(
        self,
        auth_client: AsyncClient,
        db_session: AsyncSession,
    ):
        _team_id, group_id = await _get_default_team_and_group(auth_client)

        resp = await auth_client.get("/user/me")
        user_id = resp.json()["id"]

        # Create device
        resp = await auth_client.post("/devices/", json={"name": "WorkerTestDev", "group_id": group_id})
        device_id = resp.json()["id"]

        # Insert some logs for this device
        log1 = Log(
            device_id=device_id,
            time=datetime.now(UTC),
            content="Alert 1",
            severity=LogSeverity.critical,
        )
        log2 = Log(
            device_id=device_id,
            time=datetime.now(UTC),
            content="Alert 2",
            severity=LogSeverity.high,
        )
        db_session.add_all([log1, log2])
        await db_session.flush()

        # Add LogSeen
        seen = LogSeen(user_id=user_id, log_id=log1.id)
        db_session.add(seen)

        # Add Exclusion linked to log2
        exc = Exclusion(
            name="Exclusion linked to log",
            jsonata_query="$alert = true",
            alert_id=log2.id,
        )
        db_session.add(exc)
        await db_session.flush()
        await db_session.execute(insert(exclusion_device_groups).values(exclusion_id=exc.id, device_group_id=group_id))
        await db_session.commit()

        # Soft delete device
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200

        # Verify device is marked deleted in db
        dev_res = await db_session.execute(select(Device).where(Device.id == device_id))
        dev = dev_res.scalar_one_or_none()
        assert dev is not None
        assert dev.deleted_at is not None

        # Run process_deleted_devices
        await process_deleted_devices(db_session)

        # Device should now be permanently deleted from DB
        dev_res = await db_session.execute(select(Device).where(Device.id == device_id))
        assert dev_res.scalar_one_or_none() is None

        # Logs should be deleted
        logs_res = await db_session.execute(select(Log).where(Log.device_id == device_id))
        assert len(logs_res.scalars().all()) == 0

        # LogSeen should be deleted
        seen_res = await db_session.execute(select(LogSeen).where(LogSeen.log_id.in_([log1.id, log2.id])))
        assert len(seen_res.scalars().all()) == 0

        # Exclusion alert_id should be cleared (set to None)
        exc_res = await db_session.execute(select(Exclusion).where(Exclusion.id == exc.id))
        exc_obj = exc_res.scalar_one()
        assert exc_obj.alert_id is None

        # Association in device_group_devices should be gone
        assoc_res = await db_session.execute(select(device_group_devices).where(device_group_devices.c.device_id == device_id))
        assert len(assoc_res.all()) == 0

    async def test_batch_deletion_of_large_logs(
        self,
        auth_client: AsyncClient,
        db_session: AsyncSession,
    ):
        _, group_id = await _get_default_team_and_group(auth_client)

        resp = await auth_client.post("/devices/", json={"name": "BigLogDev", "group_id": group_id})
        device_id = resp.json()["id"]

        # Insert 1500 logs to test multiple batches
        logs = [
            Log(
                device_id=device_id,
                time=datetime.now(UTC),
                content=f"Log {i}",
                severity=LogSeverity.low,
            )
            for i in range(1500)
        ]
        db_session.add_all(logs)
        await db_session.commit()

        # Mark deleted
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200

        # Run delete_device_logs_and_device directly
        await delete_device_logs_and_device(device_id, db_session)

        # Verify device and all 1500 logs are gone
        dev_res = await db_session.execute(select(Device).where(Device.id == device_id))
        assert dev_res.scalar_one_or_none() is None

        logs_res = await db_session.execute(select(Log).where(Log.device_id == device_id))
        assert len(logs_res.scalars().all()) == 0

    async def test_space_usage_excludes_deleted_device(
        self,
        auth_client: AsyncClient,
        db_session: AsyncSession,
    ):
        team_id, group_id = await _get_default_team_and_group(auth_client)

        # Create device
        resp = await auth_client.post("/devices/", json={"name": "SpaceDev", "group_id": group_id})
        device_id = resp.json()["id"]

        # Set space used
        dev_res = await db_session.execute(select(Device).where(Device.id == device_id))
        dev = dev_res.scalar_one()
        dev.total_space_used = 1048576  # 1MB
        await db_session.commit()

        # Compute space usage
        await compute_all_space_usage(db_session)

        # Group and Team should have 1MB
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.json()["total_space_used"] == 1048576

        resp = await auth_client.get("/teams/")
        team_data = next(t for t in resp.json() if t["id"] == team_id)
        assert team_data["total_space_used"] == 1048576

        # Soft delete device
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200

        # Recompute space usage
        await compute_all_space_usage(db_session)

        # Group and Team usage should now drop to 0
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.json()["total_space_used"] == 0

        resp = await auth_client.get("/teams/")
        team_data = next(t for t in resp.json() if t["id"] == team_id)
        assert team_data["total_space_used"] == 0
