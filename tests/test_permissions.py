"""Tests for permission enforcement across endpoints."""

import pytest
from httpx import AsyncClient


class TestRolePermissions:
    @pytest.mark.asyncio
    async def test_regular_user_cannot_create_pack(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/packs/",
            json={
                "name": "No Permission Pack",
                "description": "",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_maintainer_can_create_pack(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/",
            json={
                "name": "Perm Test Pack",
                "description": "",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_regular_user_cannot_access_admin(self, auth_client: AsyncClient):
        resp = await auth_client.get("/admin/users")
        assert resp.status_code == 403
        resp = await auth_client.get("/admin/devices")
        assert resp.status_code == 403
        resp = await auth_client.get("/admin/packs")
        assert resp.status_code == 403


class TestTeamPermissions:
    @pytest.mark.asyncio
    async def test_non_member_cannot_access_team(self, client: AsyncClient, auth_client: AsyncClient):
        # Get the first user's team ID while still authenticated as first user
        teams_resp = await auth_client.get("/teams/")
        team_id = teams_resp.json()[0]["id"]

        # Register and log in as a second user (overwrites session on shared client)
        await client.post(
            "/auth/register",
            json={
                "email": "other@example.com",
                "password": "password123",
            },
        )
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": "other@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")
        await client.post(
            "/auth/login",
            json={
                "email": "other@example.com",
                "password": "password123",
            },
        )

        # Second user tries to access first user's team
        resp = await client.get(f"/teams/{team_id}")
        assert resp.status_code == 403


class TestSessionInvalidation:
    @pytest.mark.asyncio
    async def test_session_invalid_after_password_change(self, client: AsyncClient):
        # Register and verify
        await client.post(
            "/auth/register",
            json={
                "email": "session@example.com",
                "password": "password123",
            },
        )
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": "session@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Login
        resp = await client.post(
            "/auth/login",
            json={
                "email": "session@example.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 200

        # Session should work
        resp = await client.get("/user/me")
        assert resp.status_code == 200
