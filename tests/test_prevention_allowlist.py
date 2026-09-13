import pytest
from httpx import AsyncClient


async def _get_default_group_id(client: AsyncClient) -> int:
    resp = await client.get("/teams/")
    team_id = resp.json()[0]["id"]
    resp = await client.get(f"/teams/{team_id}/groups")
    return resp.json()[0]["id"]


@pytest.mark.asyncio
class TestPreventionAllowlist:
    async def test_create_prevention_allowlist_entry(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post(
            f"/prevention-allowlist/groups/{group_id}",
            json={
                "entry_type": "path",
                "value": "encrypted_path_value",
                "description": "Test path",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry_type"] == "path"
        assert data["value"] == "encrypted_path_value"
        assert data["description"] == "Test path"
        assert data["device_group_id"] == group_id

    async def test_create_image_type_entry(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post(
            f"/prevention-allowlist/groups/{group_id}",
            json={
                "entry_type": "image",
                "value": "encrypted_image_value",
                "description": "Test image",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry_type"] == "image"
        assert data["value"] == "encrypted_image_value"

    async def test_list_group_entries(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        # Create an entry first
        await auth_client.post(
            f"/prevention-allowlist/groups/{group_id}",
            json={
                "entry_type": "path",
                "value": "encrypted_path_value",
            },
        )

        resp = await auth_client.get(f"/prevention-allowlist/groups/{group_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert any(e["value"] == "encrypted_path_value" for e in data)

    async def test_delete_entry(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        create_resp = await auth_client.post(
            f"/prevention-allowlist/groups/{group_id}",
            json={
                "entry_type": "path",
                "value": "to_be_deleted",
            },
        )
        entry_id = create_resp.json()["id"]

        del_resp = await auth_client.delete(f"/prevention-allowlist/{entry_id}")
        assert del_resp.status_code == 200

        # Verify deletion
        list_resp = await auth_client.get(f"/prevention-allowlist/groups/{group_id}")
        assert not any(e["id"] == entry_id for e in list_resp.json())

    async def test_unauthorized_access(self, client: AsyncClient):
        resp = await client.get("/prevention-allowlist/groups/1")
        assert resp.status_code == 401

    async def test_group_detail_includes_prevention_allowlists(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        await auth_client.post(
            f"/prevention-allowlist/groups/{group_id}",
            json={
                "entry_type": "path",
                "value": "detail_path_val",
            },
        )

        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "prevention_allowlists" in data
        assert any(e["value"] == "detail_path_val" for e in data["prevention_allowlists"])

    async def test_device_endpoint(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Create an entry
        await auth_client.post(
            f"/prevention-allowlist/groups/{group_id}",
            json={
                "entry_type": "path",
                "value": "device_path_val",
            },
        )

        # Create device
        dev_resp = await auth_client.post("/devices/", json={"name": "TestDevice", "group_id": group_id})
        assert dev_resp.status_code == 200
        token = dev_resp.json()["token"]

        # Create unauth client and login as device
        device_client = AsyncClient(transport=auth_client._transport, base_url=auth_client.base_url)
        login_resp = await device_client.post("/auth/device/login", json={"token": token})
        assert login_resp.status_code == 200

        # Fetch device entries
        entries_resp = await device_client.get("/prevention-allowlist/device")
        assert entries_resp.status_code == 200
        data = entries_resp.json()

        assert "entries" in data
        assert "group_keys" in data
        assert any(e["value"] == "device_path_val" for e in data["entries"])
        assert str(group_id) in data["group_keys"]
