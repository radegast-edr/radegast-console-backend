import pytest
from httpx import AsyncClient


class TestDashboard:
    @pytest.mark.asyncio
    async def test_get_dashboard_data(self, admin_client: AsyncClient):
        resp = await admin_client.get("/dashboard/")
        assert resp.status_code == 200
        data = resp.json()
        assert "teams" in data
        assert "groups" in data
        assert "devices" in data
        assert "logs" in data
        assert "team_device_counts" in data
        assert "group_device_counts" in data
        assert "device_groups_map" in data
        assert "device_teams_map" in data

    @pytest.mark.asyncio
    async def test_get_dashboard_data_unauthorized(self, client: AsyncClient):
        resp = await client.get("/dashboard/")
        assert resp.status_code == 401
