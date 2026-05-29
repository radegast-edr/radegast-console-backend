import pytest
from httpx import AsyncClient


async def _get_default_group_id(client: AsyncClient) -> int:
    resp = await client.get("/teams/")
    team_id = resp.json()[0]["id"]
    resp = await client.get(f"/teams/{team_id}/groups")
    return resp.json()[0]["id"]


@pytest.mark.asyncio
class TestDeviceCreation:
    async def test_create_device(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Server-01", "group_id": group_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Server-01"
        assert "token" in data
        assert len(data["token"]) > 0

    async def test_list_devices(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        await auth_client.post("/devices/", json={"name": "Server-02", "group_id": group_id})
        resp = await auth_client.get("/devices/")
        assert resp.status_code == 200

    async def test_create_device_unauthenticated(self, client: AsyncClient):
        resp = await client.post("/devices/", json={"name": "Ghost", "group_id": 1})
        assert resp.status_code == 401

    async def test_list_devices_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/devices/")
        assert resp.status_code == 401

    async def test_create_device_in_nonexistent_group(self, auth_client: AsyncClient):
        resp = await auth_client.post("/devices/", json={"name": "Ghost", "group_id": 99999})
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceLogin:
    async def test_device_login(self, auth_client: AsyncClient, client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Agent-01", "group_id": group_id})
        token = resp.json()["token"]

        resp = await client.post("/auth/device/login", json={"token": token})
        assert resp.status_code == 200
        assert "radegast_session" in resp.cookies

    async def test_device_login_invalid_token(self, client: AsyncClient):
        resp = await client.post("/auth/device/login", json={"token": "invalid"})
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestDeviceSigningKey:
    async def test_set_signing_key(self, auth_client: AsyncClient, client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Agent-02", "group_id": group_id})
        token = resp.json()["token"]
        await client.post("/auth/device/login", json={"token": token})

        resp = await client.post(
            "/devices/signing-key",
            json={"signature_public_key": "test-public-key-data"},
        )
        assert resp.status_code == 200

    async def test_set_signing_key_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/devices/signing-key",
            json={"signature_public_key": "test-key"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestDeviceGroupAssignment:
    async def test_add_device_to_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        resp = await auth_client.post("/devices/", json={"name": "Agent-03", "group_id": group_id})
        device_id = resp.json()["id"]

        # Add device to same group again (idempotent)
        resp = await auth_client.post(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 200

    async def test_add_device_to_nonexistent_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Agent-04", "group_id": group_id})
        device_id = resp.json()["id"]
        resp = await auth_client.post(f"/devices/{device_id}/groups/99999")
        assert resp.status_code == 404

    async def test_add_nonexistent_device_to_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post(f"/devices/99999/groups/{group_id}")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceDeletion:
    async def test_delete_device(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "ToDelete", "group_id": group_id})
        device_id = resp.json()["id"]
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200

    async def test_delete_nonexistent_device(self, auth_client: AsyncClient):
        resp = await auth_client.delete("/devices/99999")
        assert resp.status_code == 404
