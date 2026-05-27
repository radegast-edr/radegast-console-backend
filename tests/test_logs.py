import pytest
from datetime import datetime
from httpx import AsyncClient


@pytest.mark.asyncio
class TestLogSubmission:
    async def test_submit_log(self, auth_client: AsyncClient, client: AsyncClient):
        # Create device and login
        resp = await auth_client.post("/devices/", json={"name": "Logger-01"})
        token = resp.json()["token"]
        device_id = resp.json()["id"]

        # Add device to a group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]
        await auth_client.post(f"/devices/{device_id}/groups/{group_id}")

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        # Submit log
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.utcnow().isoformat(),
                "content": "encrypted-log-content-here",
                "signature": "sig-data",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "encrypted-log-content-here"


@pytest.mark.asyncio
class TestLogRetrieval:
    async def test_list_logs_with_permission(self, auth_client: AsyncClient, client: AsyncClient):
        # Create device, add to group, login as device, submit log
        resp = await auth_client.post("/devices/", json={"name": "Logger-02"})
        token = resp.json()["token"]
        device_id = resp.json()["id"]

        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]
        await auth_client.post(f"/devices/{device_id}/groups/{group_id}")

        await client.post("/auth/device/login", json={"token": token})
        await client.post(
            "/logs/",
            json={
                "time": datetime.utcnow().isoformat(),
                "content": "test-log-data",
            },
        )

        # List logs as user (has permission_logs: read on default team)
        resp = await auth_client.get("/logs/")
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) >= 1


@pytest.mark.asyncio
class TestEncryptionKeys:
    async def test_get_encryption_keys(self, auth_client: AsyncClient, client: AsyncClient):
        # Create device and login
        resp = await auth_client.post("/devices/", json={"name": "Logger-03"})
        token = resp.json()["token"]
        device_id = resp.json()["id"]

        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]
        await auth_client.post(f"/devices/{device_id}/groups/{group_id}")

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        resp = await client.get("/logs/encryption-keys")
        assert resp.status_code == 200
        # May be empty if no keys set up yet, but should not error
        assert isinstance(resp.json(), list)
