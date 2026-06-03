import pytest
from datetime import datetime, timezone
from httpx import AsyncClient


@pytest.mark.asyncio
class TestLogSubmission:
    async def test_submit_log(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-01", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        # Submit log
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "signature": "sig-data",
                "severity": "low",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "encrypted-log-content-here"
        assert resp.json()["severity"] == "low"

    async def test_submit_log_invalid_severity(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device
        resp = await auth_client.post("/devices/", json={"name": "Logger-01b", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        # Submit log with invalid severity
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "severity": "invalid-severity",
            },
        )
        assert resp.status_code == 422

    async def test_submit_log_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "some-content",
            },
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestLogRetrieval:
    async def test_list_logs_with_permission(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-02", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device (overwrites user session on shared client)
        await client.post("/auth/device/login", json={"token": token})
        await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "test-log-data",
                "severity": "critical",
            },
        )

        # Re-login as user to check logs (client == auth_client, device login overwrote session)
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # List logs as user (has permission_logs: read on default team)
        resp = await auth_client.get("/logs/")
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) >= 1
        
        # Check that severity is returned correctly in log list
        test_log = next(log for log in logs if log["content"] == "test-log-data")
        assert test_log["severity"] == "critical"

    async def test_list_logs_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/logs/")
        assert resp.status_code == 401

    async def test_list_logs_filtered_by_device(
        self, auth_client: AsyncClient, client: AsyncClient
    ):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-03", "group_id": group_id})
        token = resp.json()["token"]
        device_id = resp.json()["id"]

        # Login as device and submit log (overwrites user session on shared client)
        await client.post("/auth/device/login", json={"token": token})
        await client.post(
            "/logs/",
            json={"time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "content": "filtered-log"},
        )

        # Re-login as user to query logs (client == auth_client)
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        resp = await auth_client.get(f"/logs/?device_id={device_id}")
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) >= 1
        assert all(log["device_id"] == device_id for log in logs)


@pytest.mark.asyncio
class TestEncryptionKeys:
    async def test_get_encryption_keys(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-04", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        resp = await client.get("/logs/encryption-keys")
        assert resp.status_code == 200
        # May be empty if no keys set up yet, but should not error
        assert isinstance(resp.json(), list)

    async def test_encryption_keys_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.get("/logs/encryption-keys")
        assert resp.status_code == 403
