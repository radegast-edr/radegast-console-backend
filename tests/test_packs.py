import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User, UserRole
from app.services.auth import create_signed_token


@pytest.mark.asyncio
class TestPackCreation:
    async def test_create_pack_as_maintainer(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/",
            json={"name": "EDR Base Rules", "description": "Base detection rules"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "EDR Base Rules"

    async def test_create_pack_as_regular_user_fails(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/packs/",
            json={"name": "Forbidden Pack", "description": "Should fail"},
        )
        assert resp.status_code == 403

    async def test_create_duplicate_pack_fails(self, maintainer_client: AsyncClient):
        await maintainer_client.post(
            "/packs/",
            json={"name": "Unique Pack", "description": "First"},
        )
        resp = await maintainer_client.post(
            "/packs/",
            json={"name": "Unique Pack", "description": "Duplicate"},
        )
        assert resp.status_code == 400

    async def test_list_packs(self, maintainer_client: AsyncClient):
        await maintainer_client.post(
            "/packs/", json={"name": "List Test Pack", "description": "For listing"}
        )
        resp = await maintainer_client.get("/packs/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_list_packs_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/packs/")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestPackGet:
    async def test_get_pack(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Gettable Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        resp = await maintainer_client.get(f"/packs/{pack_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Gettable Pack"
        assert data["id"] == pack_id

    async def test_get_nonexistent_pack(self, client: AsyncClient):
        resp = await client.get("/packs/99999")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestPackVersions:
    async def test_upload_version(self, maintainer_client: AsyncClient):
        # Create pack first
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Versioned Pack", "description": "Has versions"}
        )
        pack_id = resp.json()["id"]

        # Upload version with release notes
        zip_content = b"PK\x03\x04" + b"\x00" * 100  # Minimal zip-like content
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack-1.0.0.zip", zip_content, "application/zip")},
            data={"release_notes": "Initial release notes"},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.0.0"
        assert resp.json()["release_notes"] == "Initial release notes"

        # Upload another higher version without release notes
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.1.0",
            files={"file": ("pack-1.1.0.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.1.0"
        assert resp.json()["release_notes"] is None

    async def test_upload_version_nonexistent_pack(self, maintainer_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            "/packs/99999/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 404

    async def test_upload_duplicate_version_fails(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Dup Version Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 400

    async def test_list_versions(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Multi Version", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.1.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )

        resp = await maintainer_client.get(f"/packs/{pack_id}/versions")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_upload_invalid_version_format_fails(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Semver Format Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        for invalid_version in ["1.0", "1.0.0.0", "v1.0.0", "abc"]:
            resp = await maintainer_client.post(
                f"/packs/{pack_id}/versions?version={invalid_version}",
                files={"file": ("pack.zip", zip_content, "application/zip")},
            )
            assert resp.status_code == 400

    async def test_upload_older_version_fails(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Older Version Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200

        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=0.9.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 400
        assert "must be higher than existing version" in resp.json()["detail"]


@pytest.mark.asyncio
class TestPackEnabling:
    async def test_enable_pack_for_group(self, maintainer_client: AsyncClient):
        # Create pack and version as maintainer
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Enable Test Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        version_id = resp.json()["id"]

        # Use maintainer's own team group
        resp = await maintainer_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await maintainer_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Enable pack for group
        resp = await maintainer_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": version_id, "autoupdate": True},
        )
        assert resp.status_code == 200

    async def test_list_enabled_packs(self, maintainer_client: AsyncClient):
        # Create and enable pack
        resp = await maintainer_client.post(
            "/packs/", json={"name": "List Enabled Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        version_id = resp.json()["id"]

        resp = await maintainer_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await maintainer_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        await maintainer_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": version_id},
        )

        resp = await maintainer_client.get(f"/packs/groups/{group_id}/enabled")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_disable_pack(self, maintainer_client: AsyncClient):
        # Create and enable a pack
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Disable Test Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        version_id = resp.json()["id"]

        resp = await maintainer_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await maintainer_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await maintainer_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": version_id},
        )
        enabled_id = resp.json()["id"]

        # Disable the pack
        resp = await maintainer_client.delete(f"/packs/groups/{group_id}/enabled/{enabled_id}")
        assert resp.status_code == 200

        # Verify it's removed
        resp = await maintainer_client.get(f"/packs/groups/{group_id}/enabled")
        enabled_ids = [e["id"] for e in resp.json()]
        assert enabled_id not in enabled_ids

    async def test_disable_nonexistent_enabled_pack(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await maintainer_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]
        resp = await maintainer_client.delete(f"/packs/groups/{group_id}/enabled/99999")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDevicePacks:
    async def _setup_device_with_pack(self, maintainer_client: AsyncClient, client: AsyncClient):
        """Helper: create pack+version, device, add device to group, enable pack, login as device."""
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Device Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        version_id = resp.json()["id"]

        # Get maintainer's default group
        resp = await maintainer_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await maintainer_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group on creation)
        resp = await maintainer_client.post("/devices/", json={"name": "DevPackAgent", "group_id": group_id})
        device_id = resp.json()["id"]
        device_token = resp.json()["token"]

        await maintainer_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": version_id},
        )

        # Login as device using client (same object as maintainer_client)
        await client.post("/auth/device/login", json={"token": device_token})

        return {"version_id": version_id, "pack_id": pack_id, "group_id": group_id, "device_id": device_id, "token": device_token}

    async def test_device_available_packs(self, maintainer_client: AsyncClient, client: AsyncClient):
        info = await self._setup_device_with_pack(maintainer_client, client)

        # Check last_seen is None initially (wait, _setup_device_with_pack already logged in as device, so let's log back in as maintainer first)
        await client.post("/auth/login", json={"email": "maintainer@example.com", "password": "MaintainerPass123!"})
        resp = await client.get(f"/devices/{info['device_id']}")
        assert resp.status_code == 200
        assert resp.json()["last_seen"] is None

        # Log back in as device
        await client.post("/auth/device/login", json={"token": info["token"]})
        resp = await client.get("/packs/device/available")
        assert resp.status_code == 200
        packs = resp.json()
        assert len(packs) >= 1
        assert any(p["pack_version_id"] == info["version_id"] for p in packs)

        # Log back in as maintainer to check last_seen is updated
        await client.post("/auth/login", json={"email": "maintainer@example.com", "password": "MaintainerPass123!"})
        resp = await client.get(f"/devices/{info['device_id']}")
        assert resp.status_code == 200
        assert resp.json()["last_seen"] is not None

    async def test_device_download_pack_not_found(
        self, auth_client: AsyncClient, client: AsyncClient
    ):
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await auth_client.post("/devices/", json={"name": "DLAgent", "group_id": group_id})
        token = resp.json()["token"]
        await client.post("/auth/device/login", json={"token": token})

        resp = await client.get("/packs/device/download/99999")
        assert resp.status_code == 404

    async def test_device_download_pack(
        self, maintainer_client: AsyncClient, client: AsyncClient
    ):
        info = await self._setup_device_with_pack(maintainer_client, client)
        resp = await client.get(f"/packs/device/download/{info['version_id']}")
        assert resp.status_code == 200

    async def test_device_available_packs_requires_device_session(
        self, auth_client: AsyncClient
    ):
        resp = await auth_client.get("/packs/device/available")
        assert resp.status_code == 403

    async def test_user_download_pack(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.post(
            "/packs/", json={"name": "User Download Pack", "description": "Test"}
        )
        pack_id = resp.json()["id"]

        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        version_id = resp.json()["id"]

        resp = await maintainer_client.get(f"/packs/download/{version_id}")
        assert resp.status_code == 200
        assert resp.content == zip_content

    async def test_user_download_pack_not_found(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.get("/packs/download/99999")
        assert resp.status_code == 404

    async def test_user_download_pack_requires_login(self, client: AsyncClient):
        resp = await client.get("/packs/download/1")
        assert resp.status_code == 401

    async def test_autoupdate_enabled_on_publish(self, maintainer_client: AsyncClient):
        # 1. Create a pack
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Autoupdate Pack", "description": "Test"}
        )
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        # 2. Upload version 1.0.0
        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200
        v1_id = resp.json()["id"]

        # 3. Get team
        resp = await maintainer_client.get("/teams/")
        team_id = resp.json()[0]["id"]

        # 4. Create two groups under this team
        resp = await maintainer_client.post(
            f"/teams/{team_id}/groups",
            json={"name": "Group AutoUpdate Enabled"}
        )
        assert resp.status_code == 200
        group_autoupdate_id = resp.json()["id"]

        resp = await maintainer_client.post(
            f"/teams/{team_id}/groups",
            json={"name": "Group AutoUpdate Disabled"}
        )
        assert resp.status_code == 200
        group_no_autoupdate_id = resp.json()["id"]

        # 5. Enable pack for both groups (one with autoupdate=True, one with autoupdate=False)
        resp = await maintainer_client.post(
            f"/packs/groups/{group_autoupdate_id}/enable",
            json={"pack_version_id": v1_id, "autoupdate": True},
        )
        assert resp.status_code == 200

        resp = await maintainer_client.post(
            f"/packs/groups/{group_no_autoupdate_id}/enable",
            json={"pack_version_id": v1_id, "autoupdate": False},
        )
        assert resp.status_code == 200

        # 6. Upload version 1.1.0 (publishing new version)
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.1.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200
        v2_id = resp.json()["id"]

        # 7. Check Enabled Pack versions for both groups
        # For group with autoupdate=True, it should now be v2_id
        resp = await maintainer_client.get(f"/packs/groups/{group_autoupdate_id}/enabled")
        assert resp.status_code == 200
        enabled_packs = resp.json()
        assert len(enabled_packs) == 1
        assert enabled_packs[0]["pack_version_id"] == v2_id

        # For group with autoupdate=False, it should still be v1_id
        resp = await maintainer_client.get(f"/packs/groups/{group_no_autoupdate_id}/enabled")
        assert resp.status_code == 200
        enabled_packs = resp.json()
        assert len(enabled_packs) == 1
        assert enabled_packs[0]["pack_version_id"] == v1_id


@pytest.mark.asyncio
class TestPackPermissionsNew:
    async def test_create_private_pack_as_regular_user_on_own_team(self, auth_client: AsyncClient):
        # 1. Get user's own team
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]

        # 2. Create private pack
        resp = await auth_client.post(
            "/packs/",
            json={"name": "User Private Pack", "description": "Private pack test", "team_ids": [team_id]},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "User Private Pack"
        assert resp.json()["team_ids"] == [team_id]

    async def test_create_private_pack_as_regular_user_invalid_team_fails(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/packs/",
            json={"name": "User Fail Private Pack", "description": "Should fail", "team_ids": [99999]},
        )
        assert resp.status_code == 404

    async def test_creator_can_update_delete_and_version(self, auth_client: AsyncClient):
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]

        resp = await auth_client.post(
            "/packs/",
            json={"name": "Creator Pack", "description": "Private pack", "team_ids": [team_id]},
        )
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        # Update description
        resp = await auth_client.patch(
            f"/packs/{pack_id}",
            json={"description": "Updated description"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated description"

        # Upload version
        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await auth_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200

        # Delete pack
        resp = await auth_client.delete(f"/packs/{pack_id}")
        assert resp.status_code == 200

    async def test_non_member_cannot_access_private_pack(self, maintainer_client: AsyncClient):
        # Maintainer creates a private pack on maintainer's own team
        resp = await maintainer_client.get("/teams/")
        m_team_id = resp.json()[0]["id"]

        resp = await maintainer_client.post(
            "/packs/",
            json={"name": "Maintainer Private Pack", "description": "Private to maintainer", "team_ids": [m_team_id]},
        )
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        # Register and login as a new regular user (overwriting session on maintainer_client)
        email = "otheruser@example.com"
        password = "OtherPass123!"
        await maintainer_client.post("/auth/register", json={"email": email, "password": password})
        
        # Verify email manually
        token = create_signed_token({"email": email}, salt="email-verify")
        await maintainer_client.get(f"/auth/verify?token={token}")

        # Login as the new user
        resp = await maintainer_client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200

        # Regular user tries to view it
        resp = await maintainer_client.get(f"/packs/{pack_id}")
        assert resp.status_code == 403

        # Regular user tries to list all packs (should not see it)
        resp = await maintainer_client.get("/packs/")
        assert resp.status_code == 200
        pack_ids = [p["id"] for p in resp.json()]
        assert pack_id not in pack_ids

        # Regular user tries to update it
        resp = await maintainer_client.patch(
            f"/packs/{pack_id}",
            json={"description": "Hack description"},
        )
        assert resp.status_code == 403

        # Regular user tries to upload version
        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 403

    async def test_deny_changing_team_pack_permission_if_would_break_constraint(self, maintainer_client: AsyncClient):
        # Maintainer creates a private pack on maintainer's own team
        resp = await maintainer_client.get("/teams/")
        m_team_id = resp.json()[0]["id"]
        m_team_name = resp.json()[0]["name"]

        resp = await maintainer_client.post(
            "/packs/",
            json={"name": "Constraint Pack", "description": "Private pack", "team_ids": [m_team_id]},
        )
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        # Try to change maintainer team's permission_pack from 'write' to 'read'
        # Since this pack only belongs to this team (which has write), changing it to 'read' should be blocked
        resp = await maintainer_client.put(
            f"/teams/{m_team_id}",
            json={"name": m_team_name, "permission_pack": "read"},
        )
        assert resp.status_code == 400
        assert "must belong to at least one team with write permission" in resp.json()["detail"]

    async def test_admin_cannot_access_other_private_pack(self, auth_client: AsyncClient, db_engine):
        # 1. Get auth_client user's own team
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]

        # 2. Create private pack as regular user
        resp = await auth_client.post(
            "/packs/",
            json={"name": "Admin Hidden Private Pack", "description": "Hidden from admin", "team_ids": [team_id]},
        )
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        # 3. Create a pack version for it
        zip_content = b"PK\x03\x04" + b"\x00" * 100
        resp = await auth_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200
        version_id = resp.json()["id"]

        # Now, register, verify, and promote an admin user, and login as admin on the same client
        admin_email = "packadmin@example.com"
        admin_password = "AdminPass123!"
        await auth_client.post("/auth/register", json={"email": admin_email, "password": admin_password})

        token = create_signed_token({"email": admin_email}, salt="email-verify")
        await auth_client.get(f"/auth/verify?token={token}")

        # Promote user to admin in DB
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(User).where(User.email == admin_email))
            user = result.scalar_one()
            user.role = UserRole.admin
            await session.commit()

        # Login as the admin
        resp = await auth_client.post("/auth/login", json={"email": admin_email, "password": admin_password})
        assert resp.status_code == 200

        # 4. Admin tries to view it
        resp = await auth_client.get(f"/packs/{pack_id}")
        assert resp.status_code == 403

        # 5. Admin tries to list all packs (should not see it)
        resp = await auth_client.get("/packs/")
        assert resp.status_code == 200
        pack_ids = [p["id"] for p in resp.json()]
        assert pack_id not in pack_ids

        # 6. Admin tries to update it
        resp = await auth_client.patch(
            f"/packs/{pack_id}",
            json={"description": "Admin hacking"},
        )
        assert resp.status_code == 403

        # 7. Admin tries to upload a version
        resp = await auth_client.post(
            f"/packs/{pack_id}/versions?version=2.0.0",
            files={"file": ("pack.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 403

        # 8. Admin tries to download version
        resp = await auth_client.get(f"/packs/download/{version_id}")
        assert resp.status_code == 403
