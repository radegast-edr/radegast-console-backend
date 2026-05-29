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


@pytest.mark.asyncio
class TestDeviceDetail:
    async def test_get_device_detail(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Detail-01", "group_id": group_id})
        device_id = resp.json()["id"]

        resp = await auth_client.get(f"/devices/{device_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == device_id
        assert data["name"] == "Detail-01"
        assert any(g["id"] == group_id for g in data["groups"])

    async def test_get_device_detail_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.get("/devices/99999")
        assert resp.status_code == 404

    async def test_remove_device_from_group_via_device(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Detail-02", "group_id": group_id})
        device_id = resp.json()["id"]

        # Add device to a second group first so we can remove from one
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "Second-Group-For-Remove"})
        second_group_id = resp.json()["id"]
        await auth_client.post(f"/devices/{device_id}/groups/{second_group_id}")

        resp = await auth_client.delete(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 200

        resp = await auth_client.get(f"/devices/{device_id}")
        group_ids = [g["id"] for g in resp.json()["groups"]]
        assert group_id not in group_ids


@pytest.mark.asyncio
class TestGroupEndpoints:
    async def test_list_groups(self, auth_client: AsyncClient):
        resp = await auth_client.get("/groups/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == group_id
        assert "teams" in data
        assert "devices" in data

    async def test_get_group_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.get("/groups/99999")
        assert resp.status_code == 404

    async def test_add_device_to_group_via_groups(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "ViaGroups-Group"})
        second_group_id = resp.json()["id"]

        # Create a device
        resp = await auth_client.post("/devices/", json={"name": "ViaGroups-01", "group_id": group_id})
        device_id = resp.json()["id"]

        resp = await auth_client.post(f"/groups/{second_group_id}/devices/{device_id}")
        assert resp.status_code == 200

        resp = await auth_client.get(f"/groups/{second_group_id}")
        device_ids = [d["id"] for d in resp.json()["devices"]]
        assert device_id in device_ids

    async def test_remove_device_from_group_via_groups(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "ViaGroups-Remove-Group"})
        second_group_id = resp.json()["id"]

        resp = await auth_client.post("/devices/", json={"name": "ViaGroups-02", "group_id": group_id})
        device_id = resp.json()["id"]
        await auth_client.post(f"/groups/{second_group_id}/devices/{device_id}")

        resp = await auth_client.delete(f"/groups/{second_group_id}/devices/{device_id}")
        assert resp.status_code == 200

    async def test_unlink_last_team_from_group_fails(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get("/groups/")
        resp = await auth_client.get(f"/groups/{group_id}")
        team_id = resp.json()["teams"][0]["id"]

        resp = await auth_client.delete(f"/groups/{group_id}/teams/{team_id}")
        assert resp.status_code == 400  # Cannot remove last team

    async def test_unlink_group_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.delete("/groups/99999/teams/1")
        assert resp.status_code in (403, 404)
