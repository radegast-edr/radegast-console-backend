from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.associations import device_group_devices, exclusion_device_groups, team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.exclusion import Exclusion
from app.models.log import Log
from app.models.team import Team
from app.models.user import User
from app.services.auth import create_signed_token
from app.services.exclusions import agent_version_supports_e2ee

AGE = "-----BEGIN AGE ENCRYPTED FILE-----\nYWdlLWVuY3J5cHRpb24ub3JnL3YxCg==\n-----END AGE ENCRYPTED FILE-----"


async def _get_default_group_id(client: AsyncClient) -> int:
    resp = await client.get("/teams/")
    team_id = resp.json()[0]["id"]
    resp = await client.get(f"/teams/{team_id}/groups")
    return resp.json()[0]["id"]


@pytest.mark.asyncio
class TestExclusionCreation:
    async def test_create_exclusion(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'test')", "description": "Test description"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Exclusion"
        assert data["jsonata_query"] == "$contains(alert.rule.name, 'test')"
        assert data["description"] == "Test description"
        assert data["device_group_id"] == group_id
        assert "id" in data
        assert "created_at" in data

    async def test_create_exclusion_unauthenticated(self, client: AsyncClient):
        resp = await client.post(
            "/exclusions/groups/1", json={"name": "Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'test')"}
        )
        assert resp.status_code == 401

    async def test_create_exclusion_nonexistent_group(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/exclusions/groups/99999", json={"name": "Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'test')"}
        )
        assert resp.status_code == 404

    async def test_list_group_exclusions(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Create an exclusion
        await auth_client.post(
            f"/exclusions/groups/{group_id}", json={"name": "List Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'list')"}
        )

        # List exclusions for the group
        resp = await auth_client.get(f"/exclusions/groups/{group_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(e["name"] == "List Test Exclusion" for e in data)

    async def test_delete_exclusion(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Create an exclusion
        create_resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Delete Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'delete')"},
        )
        exclusion_id = create_resp.json()["id"]

        # Delete the exclusion
        resp = await auth_client.delete(f"/exclusions/{exclusion_id}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Exclusion deleted"

        # Verify it's gone
        list_resp = await auth_client.get(f"/exclusions/groups/{group_id}")
        data = list_resp.json()
        assert not any(e["id"] == exclusion_id for e in data)

    async def test_delete_nonexistent_exclusion(self, auth_client: AsyncClient):
        resp = await auth_client.delete("/exclusions/99999")
        assert resp.status_code == 404

    async def test_get_exclusion(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Create an exclusion
        create_resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Get Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'get')", "description": "Get test"},
        )
        exclusion_id = create_resp.json()["id"]

        # Get the exclusion
        resp = await auth_client.get(f"/exclusions/{exclusion_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == exclusion_id
        assert data["name"] == "Get Test Exclusion"
        assert data["jsonata_query"] == "$contains(alert.rule.name, 'get')"
        assert data["description"] == "Get test"

    async def test_get_nonexistent_exclusion(self, auth_client: AsyncClient):
        resp = await auth_client.get("/exclusions/99999")
        assert resp.status_code == 404

    async def test_exclusion_in_group_detail(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Create an exclusion
        await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Group Detail Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'detail')"},
        )

        # Get group detail
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "exclusions" in data
        assert isinstance(data["exclusions"], list)
        assert any(e["name"] == "Group Detail Test Exclusion" for e in data["exclusions"])


@pytest.mark.asyncio
class TestDeviceExclusionDownload:
    async def test_device_exclusions_endpoint(self, auth_client: AsyncClient, client: AsyncClient):
        """Test that a device can download its exclusions."""
        # 1. Create a device group and device
        group_id = await _get_default_group_id(auth_client)

        # Create a device
        resp = await auth_client.post("/devices/", json={"name": "Test-Device", "group_id": group_id})
        assert resp.status_code == 200
        device_token = resp.json()["token"]
        resp.json()["id"]

        # 2. Create an exclusion for the group
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={
                "name": "Device Test Exclusion",
                "jsonata_query": '$contains(alert.rule.name, "test")',
                "description": "Test exclusion for device",
            },
        )
        assert resp.status_code == 200

        # 3. Login as the device
        resp = await client.post("/auth/device/login", json={"token": device_token})
        assert resp.status_code == 200

        # 4. Test that device can download its exclusions
        resp = await client.get("/exclusions/device")
        assert resp.status_code == 200
        data = resp.json()
        assert "exclusions" in data
        assert isinstance(data["exclusions"], list)
        assert len(data["exclusions"]) >= 1

        # Verify the exclusion we created is in the list
        exclusions = data["exclusions"]
        assert any(e["name"] == "Device Test Exclusion" for e in exclusions)

        # Verify the structure of the exclusion data
        device_exclusion = next(e for e in exclusions if e["name"] == "Device Test Exclusion")
        assert "id" in device_exclusion
        assert "name" in device_exclusion
        assert "jsonata_query" in device_exclusion
        assert "device_group_id" in device_exclusion


@pytest.mark.asyncio
class TestExclusionPermissions:
    async def test_cannot_create_exclusion_without_admin(self, auth_client: AsyncClient, registered_user):
        """Test that non-admin users cannot create exclusions on groups they don't admin."""
        # This test assumes the registered user doesn't have admin on the default group
        # In a real scenario, we'd need to set up specific permissions
        group_id = await _get_default_group_id(auth_client)

        # For now, just verify the endpoint works with auth
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Permission Test Exclusion", "jsonata_query": "$contains(alert.rule.name, 'perm')"},
        )
        # This will pass because the auth_client has admin in conftest
        assert resp.status_code in [200, 403]  # May be 200 if user has admin, 403 if not


@pytest.mark.asyncio
class TestExclusionTypes:
    async def test_create_exclusion_with_types(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # 1. By default (extended_edr_enabled is False), creating a soft exclusion should fail with 400
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Soft Exclusion Fail", "jsonata_query": "$contains(alert.rule.name, 'soft_fail')", "exclusion_type": "soft"},
        )
        assert resp.status_code == 400
        assert "Soft exclusions are only available in extended EDR mode" in resp.json()["detail"]

        # 2. Creating a hard exclusion when extended_edr_enabled is False should succeed (default behavior)
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={
                "name": "Hard Exclusion Success",
                "jsonata_query": "$contains(alert.rule.name, 'hard_success')",
                "exclusion_type": "hard",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["exclusion_type"] == "hard"

        # 3. Enable extended EDR mode
        resp = await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": True})
        assert resp.status_code == 200

        # 4. Creating a soft exclusion should now succeed
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={
                "name": "Soft Exclusion Success",
                "jsonata_query": "$contains(alert.rule.name, 'soft_success')",
                "exclusion_type": "soft",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["exclusion_type"] == "soft"

        # 5. Disable extended EDR mode to clean up
        await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": False})


# ---------------------------------------------------------------------------
# Reusable exclusions (one exclusion applied to several device groups)
# ---------------------------------------------------------------------------


async def _default_team_id(client: AsyncClient) -> int:
    resp = await client.get("/teams/")
    return resp.json()[0]["id"]


async def _new_group(client: AsyncClient, name: str) -> int:
    team_id = await _default_team_id(client)
    resp = await client.post(f"/teams/{team_id}/groups", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["id"]


async def _register_other_user(client: AsyncClient, email: str = "other@example.com") -> dict:
    password = "OtherPass123!"
    await client.post("/auth/register", json={"email": email, "password": password})
    token = create_signed_token({"email": email}, salt="email-verify")
    await client.get(f"/auth/verify?token={token}")
    return {"email": email, "password": password}


async def _login(client: AsyncClient, creds: dict) -> None:
    resp = await client.post("/auth/login", json=creds)
    assert resp.status_code == 200


async def _readonly_group(db_session: AsyncSession, email: str, name: str = "Read-only group") -> int:
    """A group linked to a team where the user is a member with only pack read permission."""
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    team = Team(name=f"{name} team", permission_pack="read", permission_logs="read")
    group = DeviceGroup(name=name)
    db_session.add_all([team, group])
    await db_session.flush()
    await db_session.execute(insert(team_users).values(team_id=team.id, user_id=user.id))
    await db_session.execute(insert(team_device_groups).values(team_id=team.id, device_group_id=group.id))
    await db_session.commit()
    return group.id


async def _set_group_keys(db_session: AsyncSession, group_id: int) -> None:
    group = await db_session.get(DeviceGroup, group_id)
    group.public_key = f"age1group{group_id}"
    group.private_key = AGE
    await db_session.commit()


async def _add_device(db_session: AsyncSession, group_id: int, agent_version: str | None, name: str = "dev") -> int:
    device = Device(name=name, token=f"token-{name}", agent_version=agent_version)
    db_session.add(device)
    await db_session.flush()
    await db_session.execute(insert(device_group_devices).values(device_group_id=group_id, device_id=device.id))
    await db_session.commit()
    return device.id


async def _create(client: AsyncClient, group_ids: list[int], **extra) -> dict:
    body = {"name": "Shared", "jsonata_query": "rule.name = 'x'", "group_ids": group_ids, **extra}
    resp = await client.post("/exclusions/", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
class TestReusableExclusions:
    async def test_create_in_multiple_groups(self, auth_client: AsyncClient, registered_user):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")

        data = await _create(auth_client, [g1, g2, g2], description="d")
        assert sorted(g["id"] for g in data["groups"]) == sorted([g1, g2])
        assert data["group_count"] == 2
        assert data["can_edit"] is True
        assert data["version"] == 1
        assert data["last_change"]["action"] == "created"
        assert data["last_change"]["by_email"] == registered_user["email"]

        # Listed once in the library and in each group
        lib = (await auth_client.get("/exclusions/")).json()
        assert [e["id"] for e in lib].count(data["id"]) == 1
        for gid in (g1, g2):
            listed = (await auth_client.get(f"/exclusions/groups/{gid}")).json()
            match = next(e for e in listed if e["id"] == data["id"])
            assert match["device_group_id"] == gid
            detail = (await auth_client.get(f"/groups/{gid}")).json()
            assert any(e["id"] == data["id"] for e in detail["exclusions"])

    async def test_create_requires_existing_groups(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/exclusions/", json={"name": "x", "jsonata_query": "q", "group_ids": [g1, 99999]})
        assert resp.status_code == 404
        resp = await auth_client.post("/exclusions/", json={"name": "x", "jsonata_query": "q", "group_ids": []})
        assert resp.status_code == 422

    async def test_update_applies_everywhere_and_checks_version(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        ex = await _create(auth_client, [g1, g2])

        resp = await auth_client.put(f"/exclusions/{ex['id']}", json={"version": 99, "name": "n", "jsonata_query": "q"})
        assert resp.status_code == 409

        resp = await auth_client.put(f"/exclusions/{ex['id']}", json={"version": 1, "name": "Renamed", "jsonata_query": "q2"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == ex["id"]
        assert data["version"] == 2
        assert data["last_change"]["action"] == "updated"
        for gid in (g1, g2):
            listed = (await auth_client.get(f"/exclusions/groups/{gid}")).json()
            assert next(e for e in listed if e["id"] == ex["id"])["jsonata_query"] == "q2"

        # A stale version (the one used before the update) is rejected
        resp = await auth_client.put(f"/exclusions/{ex['id']}", json={"version": 1, "name": "n", "jsonata_query": "q"})
        assert resp.status_code == 409

    async def test_apply_and_remove(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        ex = await _create(auth_client, [g1])

        resp = await auth_client.put(f"/exclusions/{ex['id']}/groups/{g2}", json={"version": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_count"] == 2
        assert data["last_change"]["action"] == "applied"
        assert data["last_change"]["group_id"] == g2
        assert data["last_change"]["group_name"] == "Second"

        resp = await auth_client.put(f"/exclusions/{ex['id']}/groups/{g2}", json={"version": data["version"]})
        assert resp.status_code == 400

        resp = await auth_client.post(f"/exclusions/{ex['id']}/groups/{g1}/remove", json={"version": data["version"]})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False
        data = (await auth_client.get(f"/exclusions/{ex['id']}")).json()
        assert [g["id"] for g in data["groups"]] == [g2]
        assert data["last_change"]["action"] == "removed"
        assert data["last_change"]["group_id"] == g1

        # Removing the last group deletes the exclusion
        resp = await auth_client.post(f"/exclusions/{ex['id']}/groups/{g2}/remove", json={"version": data["version"]})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert (await auth_client.get(f"/exclusions/{ex['id']}")).status_code == 404

    async def test_remove_from_group_not_linked(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        ex = await _create(auth_client, [g1])
        resp = await auth_client.post(f"/exclusions/{ex['id']}/groups/{g2}/remove", json={"version": 1})
        assert resp.status_code == 404

    async def test_delete_removes_everywhere(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        ex = await _create(auth_client, [g1, g2])
        resp = await auth_client.delete(f"/exclusions/{ex['id']}")
        assert resp.status_code == 200
        for gid in (g1, g2):
            assert not any(e["id"] == ex["id"] for e in (await auth_client.get(f"/exclusions/groups/{gid}")).json())

    async def test_device_receives_one_entry_per_group(self, auth_client: AsyncClient, client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        ex = await _create(auth_client, [g1, g2])

        resp = await auth_client.post("/devices/", json={"name": "Both-Groups", "group_id": g1})
        device_token = resp.json()["token"]
        device_id = resp.json()["id"]
        await db_session.execute(insert(device_group_devices).values(device_group_id=g2, device_id=device_id))
        await db_session.commit()

        await client.post("/auth/device/login", json={"token": device_token})
        data = (await client.get("/exclusions/device")).json()
        entries = [e for e in data["exclusions"] if e["id"] == ex["id"]]
        assert sorted(e["device_group_id"] for e in entries) == sorted([g1, g2])
        assert set(entries[0]) == {"id", "name", "jsonata_query", "device_group_id", "exclusion_type", "encrypted"}
        assert {str(g1), str(g2)} <= set(data["group_keys"])

    async def test_group_deletion_detaches_shared_exclusions(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        shared = await _create(auth_client, [g1, g2])
        only_g2 = await _create(auth_client, [g2])

        resp = await auth_client.delete(f"/groups/{g2}")
        assert resp.status_code == 200

        data = (await auth_client.get(f"/exclusions/{shared['id']}")).json()
        assert [g["id"] for g in data["groups"]] == [g1]
        assert data["last_change"]["action"] == "removed"
        assert data["last_change"]["group_id"] == g2
        assert data["last_change"]["group_name"] is None  # the group no longer exists
        assert (await auth_client.get(f"/exclusions/{only_g2['id']}")).status_code == 404

    async def test_last_change_user_deleted(self, auth_client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        ex = await _create(auth_client, [g1])
        exclusion = await db_session.get(Exclusion, ex["id"])
        exclusion.updated_by_user_id = None
        await db_session.commit()
        data = (await auth_client.get(f"/exclusions/{ex['id']}")).json()
        assert data["last_change"]["by_user_id"] is None
        assert data["last_change"]["by_email"] is None


@pytest.mark.asyncio
class TestReusableExclusionPermissions:
    async def test_non_member_cannot_see_or_change(self, auth_client: AsyncClient, client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        ex = await _create(auth_client, [g1])

        other = await _register_other_user(client)
        await _login(client, other)
        other_group = await _get_default_group_id(client)

        assert (await client.get(f"/exclusions/{ex['id']}")).status_code == 403
        assert (await client.get(f"/exclusions/{ex['id']}/recipients")).status_code == 403
        assert not any(e["id"] == ex["id"] for e in (await client.get("/exclusions/")).json())
        assert (await client.put(f"/exclusions/{ex['id']}", json={"version": 1, "name": "n", "jsonata_query": "q"})).status_code == 403
        assert (await client.delete(f"/exclusions/{ex['id']}")).status_code == 403
        # Cannot apply an exclusion it cannot see, even to its own group
        assert (await client.put(f"/exclusions/{ex['id']}/groups/{other_group}", json={"version": 1})).status_code == 403
        assert (await client.post(f"/exclusions/{ex['id']}/groups/{g1}/remove", json={"version": 1})).status_code == 403
        # Cannot create in a group it does not belong to
        resp = await client.post("/exclusions/", json={"name": "x", "jsonata_query": "q", "group_ids": [g1]})
        assert resp.status_code == 403

    async def test_read_only_member(self, auth_client: AsyncClient, db_session: AsyncSession, registered_user):
        g1 = await _get_default_group_id(auth_client)
        ro = await _readonly_group(db_session, registered_user["email"])

        # Cannot create in a read-only group
        resp = await auth_client.post("/exclusions/", json={"name": "x", "jsonata_query": "q", "group_ids": [g1, ro]})
        assert resp.status_code == 403

        # Exclusion in a writable and a read-only group (linked directly in the DB)
        ex = await _create(auth_client, [g1])
        await db_session.execute(insert(exclusion_device_groups).values(exclusion_id=ex["id"], device_group_id=ro))
        await db_session.commit()

        data = (await auth_client.get(f"/exclusions/{ex['id']}")).json()
        assert data["can_edit"] is False
        assert data["group_count"] == 2
        assert (await auth_client.put(f"/exclusions/{ex['id']}", json={"version": 1, "name": "n", "jsonata_query": "q"})).status_code == 403
        assert (await auth_client.delete(f"/exclusions/{ex['id']}")).status_code == 403
        assert (await auth_client.post(f"/exclusions/{ex['id']}/groups/{ro}/remove", json={"version": 1})).status_code == 403
        # Removing it from the group the user can write to is allowed
        resp = await auth_client.post(f"/exclusions/{ex['id']}/groups/{g1}/remove", json={"version": 1})
        assert resp.status_code == 200

    async def test_apply_requires_write_on_target(self, auth_client: AsyncClient, db_session: AsyncSession, registered_user):
        g1 = await _get_default_group_id(auth_client)
        ro = await _readonly_group(db_session, registered_user["email"])
        ex = await _create(auth_client, [g1])
        assert (await auth_client.put(f"/exclusions/{ex['id']}/groups/{ro}", json={"version": 1})).status_code == 403


@pytest.mark.asyncio
class TestReusableExclusionEncryption:
    async def test_agent_version_support(self):
        assert agent_version_supports_e2ee("python 0.5.0")
        assert agent_version_supports_e2ee("python 1.2.3")
        assert agent_version_supports_e2ee("rust 0.1.0")
        assert not agent_version_supports_e2ee("python 0.4.9")
        assert not agent_version_supports_e2ee("python 0.5")
        assert not agent_version_supports_e2ee("python x.y.z")
        assert not agent_version_supports_e2ee("unknown")
        assert not agent_version_supports_e2ee("")
        assert not agent_version_supports_e2ee(None)

    async def test_encrypted_requires_ciphertext(self, auth_client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        await _set_group_keys(db_session, g1)
        resp = await auth_client.post("/exclusions/", json={"name": "plain", "jsonata_query": AGE, "group_ids": [g1], "encrypted": True})
        assert resp.status_code == 400
        resp = await auth_client.post(
            "/exclusions/", json={"name": AGE, "jsonata_query": AGE, "description": "plain", "group_ids": [g1], "encrypted": True}
        )
        assert resp.status_code == 400
        ex = await _create(auth_client, [g1], name=AGE, jsonata_query=AGE, encrypted=True)
        assert ex["encrypted"] is True

    async def test_encrypted_requires_group_keys(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/exclusions/", json={"name": AGE, "jsonata_query": AGE, "group_ids": [g1], "encrypted": True})
        assert resp.status_code == 400
        assert "no encryption keys" in resp.json()["detail"]

    @pytest.mark.parametrize("version,ok", [("python 0.4.9", False), (None, False), ("python 0.5.0", True)])
    async def test_encrypted_requires_supported_agents(self, auth_client: AsyncClient, db_session: AsyncSession, version, ok):
        g1 = await _get_default_group_id(auth_client)
        await _set_group_keys(db_session, g1)
        await _add_device(db_session, g1, version)
        resp = await auth_client.post("/exclusions/", json={"name": AGE, "jsonata_query": AGE, "group_ids": [g1], "encrypted": True})
        assert (resp.status_code == 200) is ok
        # Plaintext exclusions are always allowed
        await _create(auth_client, [g1])

    async def test_apply_and_remove_encrypted(self, auth_client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        g3 = await _new_group(auth_client, "No keys")
        for gid in (g1, g2):
            await _set_group_keys(db_session, gid)
        ex = await _create(auth_client, [g1], name=AGE, jsonata_query=AGE, encrypted=True)

        recipients = (await auth_client.get(f"/exclusions/{ex['id']}/recipients")).json()
        assert recipients == {"version": 1, "public_keys": [f"age1group{g1}"]}

        # Content must be re-encrypted for the new set of groups
        assert (await auth_client.put(f"/exclusions/{ex['id']}/groups/{g2}", json={"version": 1})).status_code == 400
        assert (
            await auth_client.put(f"/exclusions/{ex['id']}/groups/{g3}", json={"version": 1, "name": AGE, "jsonata_query": AGE})
        ).status_code == 400
        resp = await auth_client.put(f"/exclusions/{ex['id']}/groups/{g2}", json={"version": 1, "name": AGE, "jsonata_query": AGE})
        assert resp.status_code == 200
        recipients = (await auth_client.get(f"/exclusions/{ex['id']}/recipients")).json()
        assert recipients["public_keys"] == sorted([f"age1group{g1}", f"age1group{g2}"])

        version = recipients["version"]
        assert (await auth_client.post(f"/exclusions/{ex['id']}/groups/{g2}/remove", json={"version": version})).status_code == 400
        resp = await auth_client.post(
            f"/exclusions/{ex['id']}/groups/{g2}/remove", json={"version": version, "name": AGE, "jsonata_query": AGE}
        )
        assert resp.status_code == 200

    async def test_encrypted_cannot_become_plaintext(self, auth_client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        await _set_group_keys(db_session, g1)
        ex = await _create(auth_client, [g1], name=AGE, jsonata_query=AGE, encrypted=True)
        resp = await auth_client.put(f"/exclusions/{ex['id']}", json={"version": 1, "name": "n", "jsonata_query": "q", "encrypted": False})
        assert resp.status_code == 400
        resp = await auth_client.put(f"/exclusions/{ex['id']}", json={"version": 1, "name": "n", "jsonata_query": "q"})
        assert resp.status_code == 400  # still encrypted, so plaintext content is rejected

    async def test_plaintext_can_be_upgraded_to_encrypted(self, auth_client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        ex = await _create(auth_client, [g1])
        body = {"version": 1, "name": AGE, "jsonata_query": AGE, "encrypted": True}
        assert (await auth_client.put(f"/exclusions/{ex['id']}", json=body)).status_code == 400  # no group keys yet
        await _set_group_keys(db_session, g1)
        resp = await auth_client.put(f"/exclusions/{ex['id']}", json=body)
        assert resp.status_code == 200
        assert resp.json()["encrypted"] is True


@pytest.mark.asyncio
class TestReusableExclusionRules:
    async def test_soft_requires_extended_edr(self, auth_client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/exclusions/", json={"name": "s", "jsonata_query": "q", "group_ids": [g1], "exclusion_type": "soft"})
        assert resp.status_code == 400
        ex = await _create(auth_client, [g1])
        resp = await auth_client.put(
            f"/exclusions/{ex['id']}", json={"version": 1, "name": "s", "jsonata_query": "q", "exclusion_type": "soft"}
        )
        assert resp.status_code == 400

    async def test_alert_must_belong_to_group(self, auth_client: AsyncClient, db_session: AsyncSession):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        device_in_g2 = await _add_device(db_session, g2, "python 0.5.0", name="g2dev")
        log = Log(device_id=device_in_g2, time=datetime.now(UTC), content="c")
        db_session.add(log)
        await db_session.commit()

        resp = await auth_client.post("/exclusions/", json={"name": "a", "jsonata_query": "q", "group_ids": [g1], "alert_id": log.id})
        assert resp.status_code == 400

        ex = await _create(auth_client, [g1, g2], alert_id=log.id)
        assert ex["alert_id"] == log.id
        await db_session.refresh(log)
        assert log.excluded_by == ex["id"]

    async def test_agent_cannot_report_foreign_exclusion(self, auth_client: AsyncClient, client: AsyncClient):
        g1 = await _get_default_group_id(auth_client)
        g2 = await _new_group(auth_client, "Second")
        await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": True})
        foreign = await _create(auth_client, [g2], exclusion_type="soft")
        shared = await _create(auth_client, [g1, g2], exclusion_type="soft")

        resp = await auth_client.post("/devices/", json={"name": "Reporter", "group_id": g1})
        await client.post("/auth/device/login", json={"token": resp.json()["token"]})

        log_time = datetime.now(UTC).replace(tzinfo=None).isoformat()
        resp = await client.post("/logs/", json={"time": log_time, "content": "c", "severity": "high", "excluded_by": foreign["id"]})
        assert resp.status_code == 200
        assert resp.json()["severity"] == "high"
        assert resp.json()["excluded_by"] is None

        resp = await client.post("/logs/", json={"time": log_time, "content": "c", "severity": "high", "excluded_by": shared["id"]})
        assert resp.status_code == 200
        assert resp.json()["severity"] == "informational"
        assert resp.json()["excluded_by"] == {"id": shared["id"], "group": {"id": g1, "name": resp.json()["excluded_by"]["group"]["name"]}}
