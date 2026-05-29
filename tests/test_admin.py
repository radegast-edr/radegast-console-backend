import pytest
from httpx import AsyncClient


class TestAdminUsers:
    @pytest.mark.asyncio
    async def test_list_users_as_admin(self, admin_client: AsyncClient):
        resp = await admin_client.get("/admin/users")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.asyncio
    async def test_list_users_as_regular_user(self, auth_client: AsyncClient):
        resp = await auth_client.get("/admin/users")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_user(self, admin_client: AsyncClient, client: AsyncClient):
        # Register a user to delete
        await client.post("/auth/register", json={
            "email": "deleteme@example.com",
            "password": "password123",
        })
        from app.services.auth import create_signed_token
        token = create_signed_token({"email": "deleteme@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Find user id
        resp = await admin_client.get("/admin/users")
        users = resp.json()
        target = next(u for u in users if u["email"] == "deleteme@example.com")

        resp = await admin_client.delete(f"/admin/users/{target['id']}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_cannot_delete_self(self, admin_client: AsyncClient):
        resp = await admin_client.get("/auth/me")
        admin_id = resp.json()["id"]

        resp = await admin_client.delete(f"/admin/users/{admin_id}")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user(self, admin_client: AsyncClient):
        resp = await admin_client.delete("/admin/users/99999")
        assert resp.status_code == 404


class TestAdminDevices:
    @pytest.mark.asyncio
    async def test_list_devices_as_admin(self, admin_client: AsyncClient):
        resp = await admin_client.get("/admin/devices")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_device_as_admin(self, admin_client: AsyncClient):
        # Get admin's default group
        resp = await admin_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await admin_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        create_resp = await admin_client.post("/devices/", json={"name": "Admin Delete Device", "group_id": group_id})
        device_id = create_resp.json()["id"]

        resp = await admin_client.delete(f"/admin/devices/{device_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_devices_as_regular_user(self, auth_client: AsyncClient):
        resp = await auth_client.get("/admin/devices")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_nonexistent_device(self, admin_client: AsyncClient):
        resp = await admin_client.delete("/admin/devices/99999")
        assert resp.status_code == 404


class TestAdminPacks:
    @pytest.mark.asyncio
    async def test_list_packs_as_admin(self, admin_client: AsyncClient):
        resp = await admin_client.get("/admin/packs")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_pack_as_admin(self, admin_client: AsyncClient):
        # Create a pack (admin also has maintainer capabilities since admin > maintainer)
        # But admin role IS admin, so checking
        resp = await admin_client.post("/packs/", json={
            "name": "Admin Pack", "description": "for deletion",
        })
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        resp = await admin_client.delete(f"/admin/packs/{pack_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_pack_as_regular_user(self, auth_client: AsyncClient):
        resp = await auth_client.delete("/admin/packs/1")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_nonexistent_pack(self, admin_client: AsyncClient):
        resp = await admin_client.delete("/admin/packs/99999")
        assert resp.status_code == 404
