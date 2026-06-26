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
            json={"name": "My Team", "permission_pack": "read", "permission_admin": "write"},
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
    async def test_invite_user(self, client: AsyncClient, auth_client: AsyncClient):
        from app.services.auth import create_signed_token
        from unittest.mock import patch, AsyncMock

        # Register the invited user first
        await client.post(
            "/auth/register",
            json={"email": "invited@example.com", "password": "Password123!"},
        )
        token = create_signed_token({"email": "invited@example.com"}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        resp = await auth_client.get("/teams/")
        teams = resp.json()
        team_id = teams[0]["id"]
        
        with patch("app.routers.teams.send_invite_email", new_callable=AsyncMock) as mock_send:
            resp = await auth_client.post(
                f"/teams/{team_id}/invite",
                json={"email": "invited@example.com"},
            )
            assert resp.status_code == 200
            mock_send.assert_called_once_with("invited@example.com", team_id, teams[0]["name"], "test@example.com")

    async def test_invite_unregistered_user_fails_silently(self, auth_client: AsyncClient):
        from app.routers.teams import FAILED_INVITE_ATTEMPTS
        FAILED_INVITE_ATTEMPTS.clear()

        resp = await auth_client.get("/teams/")
        teams = resp.json()
        team_id = teams[0]["id"]

        # 1st try: 400
        resp = await auth_client.post(
            f"/teams/{team_id}/invite",
            json={"email": "unregistered1@example.com"},
        )
        assert resp.status_code == 400

        # 2nd try: 400
        resp = await auth_client.post(
            f"/teams/{team_id}/invite",
            json={"email": "unregistered2@example.com"},
        )
        assert resp.status_code == 400

        # 3rd try: 400
        resp = await auth_client.post(
            f"/teams/{team_id}/invite",
            json={"email": "unregistered3@example.com"},
        )
        assert resp.status_code == 400

        # 4th try: 429
        resp = await auth_client.post(
            f"/teams/{team_id}/invite",
            json={"email": "unregistered4@example.com"},
        )
        assert resp.status_code == 429

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
        invite_token = create_signed_token({"email": "invitee@example.com", "team_id": team_id}, salt="team-invite")
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
        resp = await auth_client.post(f"/teams/{team_id}/members/{user_id}/delete", json={"group_keys": {}})
        assert resp.status_code == 400

    async def test_cannot_remove_last_admin(self, auth_client: AsyncClient):
        # Default team has permission_admin="write"; removing the only member must be blocked
        resp = await auth_client.get("/teams/")
        admin_team = next(t for t in resp.json() if t["permission_admin"] == "write")
        team_id = admin_team["id"]
        members = (await auth_client.get(f"/teams/{team_id}/members")).json()
        user_id = members[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/members/{user_id}/delete", json={"group_keys": {}})
        assert resp.status_code == 400
        assert "admin" in resp.json()["detail"].lower()

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
        invite_token = create_signed_token({"email": "second@example.com", "team_id": team_id}, salt="team-invite")
        await client.get(f"/auth/invite/accept?token={invite_token}")

        # Find second user's ID
        members = (await auth_client.get(f"/teams/{team_id}/members")).json()
        second = next(m for m in members if m["email"] == "second@example.com")

        # Remove the second user
        resp = await auth_client.post(f"/teams/{team_id}/members/{second['id']}/delete", json={"group_keys": {}})
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
        invite_token = create_signed_token({"email": "extra@example.com", "team_id": team_id}, salt="team-invite")
        await client.get(f"/auth/invite/accept?token={invite_token}")

        # Now try to remove a user that doesn't exist in the team
        resp = await auth_client.post(f"/teams/{team_id}/members/99999/delete", json={"group_keys": {}})
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
        resp = await auth_client.post(f"/teams/{second_team_id}/groups/{group_id}/link", json={"encrypted_private_key": "fake_key"})
        assert resp.status_code == 200
        assert "linked" in resp.json()["message"]

    async def test_clear_team_permissions(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        teams = resp.json()
        team_id = teams[0]["id"]

        # Clear permission_pack
        resp = await auth_client.put(
            f"/teams/{team_id}",
            json={"permission_pack": None},
        )
        assert resp.status_code == 200
        assert resp.json()["permission_pack"] is None

    async def test_link_group_to_team_no_admin_on_group_fails(self, client: AsyncClient):
        # User 1 registers and gets a default team and group.
        user1_email = "user1@example.com"
        await client.post("/auth/register", json={"email": user1_email, "password": "User1Pass123!"})
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": user1_email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Log in User 1 to get their group ID
        await client.post("/auth/login", json={"email": user1_email, "password": "User1Pass123!"})
        resp = await client.get("/teams/")
        user1_team_id = resp.json()[0]["id"]
        resp = await client.get(f"/teams/{user1_team_id}/groups")
        user1_group_id = resp.json()[0]["id"]
        await client.post("/auth/logout")

        # User 2 registers and gets their own default team and group.
        user2_email = "user2@example.com"
        await client.post("/auth/register", json={"email": user2_email, "password": "User2Pass123!"})
        token = create_signed_token({"email": user2_email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Log in User 2
        await client.post("/auth/login", json={"email": user2_email, "password": "User2Pass123!"})
        resp = await client.get("/teams/")
        user2_team_id = resp.json()[0]["id"]

        # User 2 tries to link User 1's group to User 2's team. This should fail.
        resp = await client.post(f"/teams/{user2_team_id}/groups/{user1_group_id}/link", json={"encrypted_private_key": "fake_key"})
        assert resp.status_code == 403

    async def test_add_device_to_group_no_admin_on_device_fails(self, client: AsyncClient):
        # User 1 registers and gets a default team and group.
        user1_email = "user1_dev@example.com"
        await client.post("/auth/register", json={"email": user1_email, "password": "User1Pass123!"})
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": user1_email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Log in User 1 to create a device in their group
        await client.post("/auth/login", json={"email": user1_email, "password": "User1Pass123!"})
        resp = await client.get("/teams/")
        user1_team_id = resp.json()[0]["id"]
        resp = await client.get(f"/teams/{user1_team_id}/groups")
        user1_group_id = resp.json()[0]["id"]

        # Create a device in User 1's group
        resp = await client.post("/devices/", json={"name": "User1Device", "group_id": user1_group_id})
        assert resp.status_code == 200
        device_id = resp.json()["id"]
        await client.post("/auth/logout")

        # User 2 registers and gets their own default team and group.
        user2_email = "user2_dev@example.com"
        await client.post("/auth/register", json={"email": user2_email, "password": "User2Pass123!"})
        token = create_signed_token({"email": user2_email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Log in User 2
        await client.post("/auth/login", json={"email": user2_email, "password": "User2Pass123!"})
        resp = await client.get("/teams/")
        user2_team_id = resp.json()[0]["id"]
        resp = await client.get(f"/teams/{user2_team_id}/groups")
        user2_group_id = resp.json()[0]["id"]

        # User 2 tries to add User 1's device to User 2's group. This should fail.
        resp = await client.post(f"/devices/{device_id}/groups/{user2_group_id}")
        assert resp.status_code == 403

    async def test_managing_team_transitive_membership_and_permissions(self, client: AsyncClient):
        # 1. User 1 registers. User 1 has Team 1 (admin=write by default)
        user1_email = "user1_mt@example.com"
        await client.post("/auth/register", json={"email": user1_email, "password": "User1Pass123!"})
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": user1_email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Log in User 1
        await client.post("/auth/login", json={"email": user1_email, "password": "User1Pass123!"})
        resp = await client.get("/teams/")
        user1_teams = resp.json()
        team1_id = user1_teams[0]["id"]

        # 2. Try to create Team 2 with admin=None and NO managing team (should fail with 400)
        resp = await client.post("/teams/", json={"name": "Team 2", "permission_admin": None})
        assert resp.status_code == 400

        # 3. Create Team 2 with admin=None and managing_team_id = team1_id (should succeed)
        resp = await client.post("/teams/", json={"name": "Team 2", "permission_admin": None, "managing_team_id": team1_id})
        assert resp.status_code == 200
        team2_id = resp.json()["id"]

        # 4. Create Team 3 with admin=None and managing_team_id = team2_id (valid chain leading to team1_id -> admin=write)
        resp = await client.post("/teams/", json={"name": "Team 3", "permission_admin": None, "managing_team_id": team2_id})
        assert resp.status_code == 200
        team3_id = resp.json()["id"]

        # 5. Try to create a circular dependency (Team 1 managed by Team 3, which is managed by Team 2, which is managed by Team 1)
        # Update Team 1 to be managed by Team 3. Since Team 1 has admin=write, this is valid.
        resp = await client.put(f"/teams/{team1_id}", json={"managing_team_id": team3_id})
        assert resp.status_code == 200

        # Now try to set Team 1 admin to None (this would create a cycle without admin=write anywhere)
        resp = await client.put(f"/teams/{team1_id}", json={"permission_admin": None})
        assert resp.status_code == 400

        # 6. Verify virtual membership:
        # Log out User 1, and register User 2.
        await client.post("/auth/logout")
        user2_email = "user2_mt@example.com"
        await client.post("/auth/register", json={"email": user2_email, "password": "User2Pass123!"})
        token = create_signed_token({"email": user2_email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")

        # Log in User 2
        await client.post("/auth/login", json={"email": user2_email, "password": "User2Pass123!"})
        # User 2 only belongs to Team 2's default team.
        # User 1 invites User 2 to Team 1. We must log in User 1 to invite User 2.
        await client.post("/auth/logout")
        await client.post("/auth/login", json={"email": user1_email, "password": "User1Pass123!"})

        # User 1 invites User 2 to Team 1
        resp = await client.post(f"/teams/{team1_id}/invite", json={"email": user2_email})
        assert resp.status_code == 200

        # Log out User 1, log in User 2, accept invite to Team 1
        await client.post("/auth/logout")
        await client.post("/auth/login", json={"email": user2_email, "password": "User2Pass123!"})

        invite_token = create_signed_token({"email": user2_email, "team_id": team1_id}, salt="team-invite")
        resp = await client.get(f"/auth/invite/accept?token={invite_token}")
        assert resp.status_code == 200

        # Now User 2 is a direct member of Team 1.
        # Since Team 1 manages Team 2, which manages Team 3, User 2 should be a transitive member of Team 2 and Team 3!
        # So User 2 should see Team 2 and Team 3 in their teams list!
        resp = await client.get("/teams/")
        assert resp.status_code == 200
        team_names = [t["name"] for t in resp.json()]
        assert "Team 2" in team_names
        assert "Team 3" in team_names

        # And User 2 should have admin permission on Team 2 and Team 3 (since Team 1 has admin=write, and Team 1 manages them)
        # Verify User 2 can update Team 3's name (requires admin permission on Team 3)
        resp = await client.put(f"/teams/{team3_id}", json={"name": "Team 3 Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Team 3 Updated"
