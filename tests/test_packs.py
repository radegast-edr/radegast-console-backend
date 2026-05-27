import io
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


@pytest.mark.asyncio
class TestPackVersions:
    async def test_upload_version(self, maintainer_client: AsyncClient):
        # Create pack first
        resp = await maintainer_client.post(
            "/packs/", json={"name": "Versioned Pack", "description": "Has versions"}
        )
        pack_id = resp.json()["id"]

        # Upload version
        zip_content = b"PK\x03\x04" + b"\x00" * 100  # Minimal zip-like content
        resp = await maintainer_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack-1.0.0.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.0.0"

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


@pytest.mark.asyncio
class TestPackEnabling:
    async def test_enable_pack_for_group(self, maintainer_client: AsyncClient, auth_client: AsyncClient):
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

        # Get auth_client's group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Enable pack for group
        resp = await auth_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": version_id, "autoupdate": True},
        )
        assert resp.status_code == 200

    async def test_list_enabled_packs(self, maintainer_client: AsyncClient, auth_client: AsyncClient):
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

        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        await auth_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": version_id},
        )

        resp = await auth_client.get(f"/packs/groups/{group_id}/enabled")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1
