import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestDeviceCreation:
    async def test_create_device(self, auth_client: AsyncClient):
        resp = await auth_client.post("/devices/", json={"name": "Server-01"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Server-01"
        assert "token" in data
        assert len(data["token"]) > 0

    async def test_list_devices(self, auth_client: AsyncClient):
        await auth_client.post("/devices/", json={"name": "Server-02"})
        resp = await auth_client.get("/devices/")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestDeviceLogin:
    async def test_device_login(self, auth_client: AsyncClient, client: AsyncClient):
        # Create device
        resp = await auth_client.post("/devices/", json={"name": "Agent-01"})
        token = resp.json()["token"]

        # Login as device
        resp = await client.post("/auth/device/login", json={"token": token})
        assert resp.status_code == 200
        assert "radegast_session" in resp.cookies

    async def test_device_login_invalid_token(self, client: AsyncClient):
        resp = await client.post("/auth/device/login", json={"token": "invalid"})
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestDeviceSigningKey:
    async def test_set_signing_key(self, auth_client: AsyncClient, client: AsyncClient):
        # Create and login as device
        resp = await auth_client.post("/devices/", json={"name": "Agent-02"})
        token = resp.json()["token"]
        await client.post("/auth/device/login", json={"token": token})

        # Set signing key
        resp = await client.post(
            "/devices/signing-key",
            json={"signature_public_key": "test-public-key-data"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestDeviceGroupAssignment:
    async def test_add_device_to_group(self, auth_client: AsyncClient):
        # Create device
        resp = await auth_client.post("/devices/", json={"name": "Agent-03"})
        device_id = resp.json()["id"]

        # Get default group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Add device to group
        resp = await auth_client.post(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestDeviceDeletion:
    async def test_delete_device(self, auth_client: AsyncClient):
        resp = await auth_client.post("/devices/", json={"name": "ToDelete"})
        device_id = resp.json()["id"]
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200
