import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestTeamAutoCreation:
    async def test_default_team_created(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        assert resp.status_code == 200
        teams = resp.json()
        assert len(teams) >= 1
        assert any("test@example.com's team" in t["name"] for t in teams)

    async def test_default_team_has_full_permissions(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        teams = resp.json()
        default_team = next(t for t in teams if "test@example.com" in t["name"])
        assert default_team["permission_pack"] == "write"
        assert default_team["permission_invite"] == "write"
        assert default_team["permission_admin"] == "write"
        assert default_team["permission_logs"] == "read"


@pytest.mark.asyncio
class TestTeamCRUD:
    async def test_create_team(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/teams/",
            json={"name": "My Team", "permission_pack": "read"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Team"

    async def test_get_team(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        teams = resp.json()
        team_id = teams[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}")
        assert resp.status_code == 200

    async def test_get_nonexistent_team(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/99999")
        assert resp.status_code == 404

    async def test_update_team(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        teams = resp.json()
        team_id = teams[0]["id"]
        resp = await auth_client.put(
            f"/teams/{team_id}",
            json={"name": "Updated Team"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Team"

    async def test_list_teams_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/teams/")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestTeamInvitation:
    async def test_invite_user(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        teams = resp.json()
        team_id = teams[0]["id"]
        resp = await auth_client.post(
            f"/teams/{team_id}/invite",
            json={"email": "invited@example.com"},
        )
        assert resp.status_code == 200

    async def test_accept_invitation(self, client: AsyncClient, auth_client: AsyncClient):
        from app.services.auth import create_signed_token

        # Register the invitee
        await client.post(
            "/auth/register",
            json={"email": "invitee@example.com", "password": "Password123!"},
        )
        token = create_signed_token({"email": "invitee@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Get team ID
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]

        # Create invitation token
        invite_token = create_signed_token(
            {"email": "invitee@example.com", "team_id": team_id}, salt="team-invite"
        )
        resp = await client.get(f"/auth/invite/accept?token={invite_token}")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestTeamMembers:
    async def test_list_members(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/members")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_cannot_remove_last_member(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        members = (await auth_client.get(f"/teams/{team_id}/members")).json()
        user_id = members[0]["id"]
        resp = await auth_client.delete(f"/teams/{team_id}/members/{user_id}")
        assert resp.status_code == 400

    async def test_remove_member_success(self, client: AsyncClient, auth_client: AsyncClient):
        from app.services.auth import create_signed_token

        # Register a second user
        await client.post(
            "/auth/register",
            json={"email": "second@example.com", "password": "Password123!"},
        )
        token = create_signed_token({"email": "second@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Add second user to team
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        invite_token = create_signed_token(
            {"email": "second@example.com", "team_id": team_id}, salt="team-invite"
        )
        await client.get(f"/auth/invite/accept?token={invite_token}")

        # Find second user's ID
        members = (await auth_client.get(f"/teams/{team_id}/members")).json()
        second = next(m for m in members if m["email"] == "second@example.com")

        # Remove the second user
        resp = await auth_client.delete(f"/teams/{team_id}/members/{second['id']}")
        assert resp.status_code == 200

    async def test_remove_nonexistent_member(self, auth_client: AsyncClient, client: AsyncClient):
        from app.services.auth import create_signed_token

        # Add a second user so team has more than 1 member (avoids "last member" 400 check)
        await client.post(
            "/auth/register",
            json={"email": "extra@example.com", "password": "Password123!"},
        )
        token = create_signed_token({"email": "extra@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        invite_token = create_signed_token(
            {"email": "extra@example.com", "team_id": team_id}, salt="team-invite"
        )
        await client.get(f"/auth/invite/accept?token={invite_token}")

        # Now try to remove a user that doesn't exist in the team
        resp = await auth_client.delete(f"/teams/{team_id}/members/99999")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceGroups:
    async def test_default_group_created(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        assert resp.status_code == 200
        groups = resp.json()
        assert len(groups) >= 1

    async def test_create_group(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(
            f"/teams/{team_id}/groups",
            json={"name": "Production"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Production"

    async def test_link_group_to_team(self, auth_client: AsyncClient):
        # Create a second team
        resp = await auth_client.post(
            "/teams/",
            json={"name": "Second Team", "permission_admin": "write"},
        )
        second_team_id = resp.json()["id"]

        # Get the default group from the first team
        resp = await auth_client.get("/teams/")
        first_team = next(t for t in resp.json() if "test@example.com" in t["name"])
        first_team_id = first_team["id"]
        resp = await auth_client.get(f"/teams/{first_team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Link existing group to second team
        resp = await auth_client.post(f"/teams/{second_team_id}/groups/{group_id}/link")
        assert resp.status_code == 200
        assert "linked" in resp.json()["message"]
