import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models.device_group import DeviceGroup


async def _get_default_group_id(client: AsyncClient) -> int:
    resp = await client.get("/teams/")
    team_id = resp.json()[0]["id"]
    resp = await client.get(f"/teams/{team_id}/groups")
    return resp.json()[0]["id"]


@pytest.mark.asyncio
class TestResponseSettings:
    async def test_update_response_settings(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Update response settings
        resp = await auth_client.patch(
            f"/groups/{group_id}/response",
            json={"response_enabled": True, "response_min_severity": "high"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["response_enabled"] is True
        assert data["response_min_severity"] == "high"

        # Check detail page also contains it
        detail_resp = await auth_client.get(f"/groups/{group_id}")
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["response_enabled"] is True
        assert detail_data["response_min_severity"] == "high"

    async def test_update_response_settings_unauthorized(self, client: AsyncClient):
        resp = await client.patch(
            "/groups/1/response",
            json={"response_enabled": True, "response_min_severity": "high"},
        )
        assert resp.status_code == 401

    async def test_get_device_config(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # Create a device in the group
        resp = await auth_client.post("/devices/", json={"name": "ResponseAgent", "group_id": group_id})
        assert resp.status_code == 200
        token = resp.json()["token"]

        # Create a separate client for the device so we don't contaminate the auth_client cookies
        device_client = AsyncClient(transport=auth_client._transport, base_url=auth_client.base_url)

        # Device logs in
        login_resp = await device_client.post("/auth/device/login", json={"token": token})
        assert login_resp.status_code == 200

        # Fetch device config (default off)
        config_resp = await device_client.get("/devices/config")
        assert config_resp.status_code == 200
        config_data = config_resp.json()
        assert config_data["response_enabled"] is False

        # Enable response settings for the group (using user client)
        update_resp = await auth_client.patch(
            f"/groups/{group_id}/response",
            json={"response_enabled": True, "response_min_severity": "medium"},
        )
        assert update_resp.status_code == 200

        # Fetch device config again (now enabled with medium severity)
        config_resp = await device_client.get("/devices/config")
        assert config_resp.status_code == 200
        config_data = config_resp.json()
        assert config_data["response_enabled"] is True
        assert config_data["response_min_severity"] == "medium"

    async def test_get_device_config_conflict_resolution(self, auth_client: AsyncClient):
        # 1. Get default team and group
        teams_resp = await auth_client.get("/teams/")
        team_id = teams_resp.json()[0]["id"]
        group1_id = await _get_default_group_id(auth_client)

        # Create a second group under the same team
        group2_resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "Group-2"})
        assert group2_resp.status_code == 200
        group2_id = group2_resp.json()["id"]

        # Create device in Group 1
        resp = await auth_client.post("/devices/", json={"name": "MultiGroupAgent", "group_id": group1_id})
        assert resp.status_code == 200
        device_id = resp.json()["id"]
        token = resp.json()["token"]

        # Add the same device to Group 2
        add_device_resp = await auth_client.post(
            f"/groups/{group2_id}/devices/{device_id}",
            json={"encrypted_private_key": "dummy_key"},
        )
        assert add_device_resp.status_code == 200

        # Create separate device client
        device_client = AsyncClient(transport=auth_client._transport, base_url=auth_client.base_url)

        # Device logs in
        login_resp = await device_client.post("/auth/device/login", json={"token": token})
        assert login_resp.status_code == 200

        # Enable response on both groups with different severities:
        # Group 1: high
        # Group 2: low
        await auth_client.patch(
            f"/groups/{group1_id}/response",
            json={"response_enabled": True, "response_min_severity": "high"},
        )
        await auth_client.patch(
            f"/groups/{group2_id}/response",
            json={"response_enabled": True, "response_min_severity": "low"},
        )

        # Fetch device config. It should resolve to the lowest rank severity: "low"
        config_resp = await device_client.get("/devices/config")
        assert config_resp.status_code == 200
        config_data = config_resp.json()
        assert config_data["response_enabled"] is True
        assert config_data["response_min_severity"] == "low"
