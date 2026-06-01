import pytest
from httpx import AsyncClient


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
