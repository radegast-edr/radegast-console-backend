import io
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestReleasesList:
    async def test_list_releases_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/releases/")
        # The list endpoint does not require authentication
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Should contain the stub release from the setup_test_releases fixture (0.0.1/linux/amd64)
        assert any(r["version"] == "0.0.1" and r["os"] == "linux" and r["arch"] == "amd64" for r in data)


@pytest.mark.asyncio
class TestReleasesUpload:
    async def test_upload_release_as_admin_succeeds(self, admin_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        resp = await admin_client.post(
            "/releases/",
            data={"version": "1.0.0", "os": "linux", "arch": "amd64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "1.0.0"
        assert data["os"] == "linux"
        assert data["arch"] == "amd64"

    async def test_upload_release_as_maintainer_fails(self, maintainer_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        resp = await maintainer_client.post(
            "/releases/",
            data={"version": "1.0.0", "os": "linux", "arch": "amd64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 403

    async def test_upload_release_as_user_fails(self, auth_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        resp = await auth_client.post(
            "/releases/",
            data={"version": "1.0.0", "os": "linux", "arch": "amd64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 403

    async def test_upload_release_invalid_version_fails(self, admin_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        resp = await admin_client.post(
            "/releases/",
            data={"version": "1.0", "os": "linux", "arch": "amd64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 400
        assert "semver" in resp.json()["detail"]

    async def test_upload_release_invalid_os_fails(self, admin_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        resp = await admin_client.post(
            "/releases/",
            data={"version": "1.0.0", "os": "macos", "arch": "amd64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 400
        assert "OS must be one of" in resp.json()["detail"]

    async def test_upload_release_invalid_arch_fails(self, admin_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        resp = await admin_client.post(
            "/releases/",
            data={"version": "1.0.0", "os": "linux", "arch": "386"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 400
        assert "Arch must be one of" in resp.json()["detail"]

    async def test_upload_release_duplicate_fails(self, admin_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        # First upload
        resp = await admin_client.post(
            "/releases/",
            data={"version": "1.1.0", "os": "windows", "arch": "arm64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 200

        # Second upload of same
        resp = await admin_client.post(
            "/releases/",
            data={"version": "1.1.0", "os": "windows", "arch": "arm64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )
        assert resp.status_code == 409


@pytest.mark.asyncio
class TestReleasesDownload:
    async def test_download_release_succeeds(self, auth_client: AsyncClient):
        resp = await auth_client.get("/releases/0.0.1/linux/amd64/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert b"stub" in resp.content

    async def test_download_release_nonexistent_fails(self, auth_client: AsyncClient):
        resp = await auth_client.get("/releases/0.0.1/windows/arm64/download")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestReleasesDelete:
    async def test_delete_release_as_admin_succeeds(self, admin_client: AsyncClient):
        zip_content = b"PK\x03\x04" + b"\x00" * 20
        # Upload a temp release to delete
        await admin_client.post(
            "/releases/",
            data={"version": "2.0.0", "os": "linux", "arch": "amd64"},
            files={"file": ("rustinel.zip", zip_content, "application/zip")},
        )

        # Delete it
        resp = await admin_client.delete("/releases/2.0.0/linux/amd64")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"]

        # Check it is no longer listed or downloadable
        resp = await admin_client.get("/releases/2.0.0/linux/amd64/download")
        assert resp.status_code == 404

    async def test_delete_release_as_maintainer_fails(self, maintainer_client: AsyncClient):
        resp = await maintainer_client.delete("/releases/0.0.1/linux/amd64")
        assert resp.status_code == 403

    async def test_delete_release_nonexistent_fails(self, admin_client: AsyncClient):
        resp = await admin_client.delete("/releases/9.9.9/linux/amd64")
        assert resp.status_code == 404
