import base64
import re

import pytest
from httpx import AsyncClient

from app.config import settings


async def _get_default_group_id(client: AsyncClient) -> int:
    resp = await client.get("/teams/")
    team_id = resp.json()[0]["id"]
    resp = await client.get(f"/teams/{team_id}/groups")
    return resp.json()[0]["id"]


@pytest.mark.asyncio
class TestDeviceCreation:
    async def test_create_device(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Server-01", "group_id": group_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Server-01"
        assert "token" in data
        assert len(data["token"]) > 0

    async def test_list_devices(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        await auth_client.post("/devices/", json={"name": "Server-02", "group_id": group_id})
        resp = await auth_client.get("/devices/")
        assert resp.status_code == 200

    async def test_create_device_unauthenticated(self, client: AsyncClient):
        resp = await client.post("/devices/", json={"name": "Ghost", "group_id": 1})
        assert resp.status_code == 401

    async def test_list_devices_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/devices/")
        assert resp.status_code == 401

    async def test_create_device_in_nonexistent_group(self, auth_client: AsyncClient):
        resp = await auth_client.post("/devices/", json={"name": "Ghost", "group_id": 99999})
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceLogin:
    async def test_device_login(self, auth_client: AsyncClient, client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Agent-01", "group_id": group_id})
        token = resp.json()["token"]

        resp = await client.post("/auth/device/login", json={"token": token})
        assert resp.status_code == 200
        assert "radegast_session" in resp.cookies

    async def test_device_login_invalid_token(self, client: AsyncClient):
        resp = await client.post("/auth/device/login", json={"token": "invalid"})
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestDeviceSigningKey:
    async def test_set_signing_key(self, auth_client: AsyncClient, client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Agent-02", "group_id": group_id})
        token = resp.json()["token"]
        await client.post("/auth/device/login", json={"token": token})

        resp = await client.post(
            "/devices/signing-key",
            json={"signature_public_key": "test-public-key-data"},
        )
        assert resp.status_code == 200

    async def test_set_signing_key_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/devices/signing-key",
            json={"signature_public_key": "test-key"},
        )
        assert resp.status_code == 403

    async def test_set_encryption_key_triggers_group_needs_refresh(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        
        # Verify initial state of needs_refresh is False
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.json()["private_key_needs_refresh"] is False

        # Create device
        resp = await auth_client.post("/devices/", json={"name": "Agent-EncryptionCheck", "group_id": group_id})
        token = resp.json()["token"]

        # Use separate client for device session
        device_client = AsyncClient(transport=auth_client._transport, base_url="http://test")
        await device_client.post("/auth/device/login", json={"token": token})

        # Set encryption key
        resp = await device_client.post(
            "/devices/encryption-key",
            json={"encryption_public_key": "test-encryption-key-data"},
        )
        assert resp.status_code == 200

        # Verify group needs refresh is now True
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.json()["private_key_needs_refresh"] is True



@pytest.mark.asyncio
class TestDeviceGroupAssignment:
    async def test_add_device_to_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        resp = await auth_client.post("/devices/", json={"name": "Agent-03", "group_id": group_id})
        device_id = resp.json()["id"]

        # Add device to same group again (idempotent)
        resp = await auth_client.post(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 200

    async def test_add_device_to_nonexistent_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Agent-04", "group_id": group_id})
        device_id = resp.json()["id"]
        resp = await auth_client.post(f"/devices/{device_id}/groups/99999")
        assert resp.status_code == 404

    async def test_add_nonexistent_device_to_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post(f"/devices/99999/groups/{group_id}")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceDeletion:
    async def test_delete_device(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "ToDelete", "group_id": group_id})
        device_id = resp.json()["id"]
        resp = await auth_client.delete(f"/devices/{device_id}")
        assert resp.status_code == 200

    async def test_delete_nonexistent_device(self, auth_client: AsyncClient):
        resp = await auth_client.delete("/devices/99999")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeviceDetail:
    async def test_get_device_detail(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Detail-01", "group_id": group_id})
        device_id = resp.json()["id"]

        resp = await auth_client.get(f"/devices/{device_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == device_id
        assert data["name"] == "Detail-01"
        assert "agent_version" in data
        assert "rustinel_version" in data
        assert any(g["id"] == group_id for g in data["groups"])

    async def test_device_checkin_updates_versions(self, auth_client: AsyncClient, client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Checkin-01", "group_id": group_id})
        device_id = resp.json()["id"]
        token = resp.json()["token"]

        device_client = AsyncClient(transport=client._transport, base_url="http://test")
        resp = await device_client.post("/auth/device/login", json={"token": token})
        assert resp.status_code == 200

        resp = await device_client.get("/packs/device/available?agent_version=2.0.1&rustinel_version=1.4.2")
        assert resp.status_code == 200

        resp = await auth_client.get(f"/devices/{device_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_version"] == "2.0.1"
        assert data["rustinel_version"] == "1.4.2"

    async def test_get_device_detail_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.get("/devices/99999")
        assert resp.status_code == 404

    async def test_remove_device_from_group_via_device(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.post("/devices/", json={"name": "Detail-02", "group_id": group_id})
        device_id = resp.json()["id"]

        # Add device to a second group first so we can remove from one
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "Second-Group-For-Remove"})
        second_group_id = resp.json()["id"]
        await auth_client.post(f"/devices/{device_id}/groups/{second_group_id}")

        resp = await auth_client.delete(f"/devices/{device_id}/groups/{group_id}")
        assert resp.status_code == 200

        resp = await auth_client.get(f"/devices/{device_id}")
        group_ids = [g["id"] for g in resp.json()["groups"]]
        assert group_id not in group_ids


@pytest.mark.asyncio
class TestGroupEndpoints:
    async def test_list_groups(self, auth_client: AsyncClient):
        resp = await auth_client.get("/groups/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_group(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get(f"/groups/{group_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == group_id
        assert "teams" in data
        assert "devices" in data

    async def test_get_group_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.get("/groups/99999")
        assert resp.status_code == 404

    async def test_add_device_to_group_via_groups(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "ViaGroups-Group"})
        second_group_id = resp.json()["id"]

        # Create a device
        resp = await auth_client.post("/devices/", json={"name": "ViaGroups-01", "group_id": group_id})
        device_id = resp.json()["id"]

        resp = await auth_client.post(f"/groups/{second_group_id}/devices/{device_id}", json={"encrypted_private_key": "fake_key"})
        assert resp.status_code == 200

        resp = await auth_client.get(f"/groups/{second_group_id}")
        device_ids = [d["id"] for d in resp.json()["devices"]]
        assert device_id in device_ids

    async def test_remove_device_from_group_via_groups(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.post(f"/teams/{team_id}/groups", json={"name": "ViaGroups-Remove-Group"})
        second_group_id = resp.json()["id"]

        resp = await auth_client.post("/devices/", json={"name": "ViaGroups-02", "group_id": group_id})
        device_id = resp.json()["id"]
        await auth_client.post(f"/groups/{second_group_id}/devices/{device_id}", json={"encrypted_private_key": "fake_key"})

        resp = await auth_client.post(f"/groups/{second_group_id}/devices/{device_id}/remove", json={"encrypted_private_key": "fake_key"})
        assert resp.status_code == 200

    async def test_unlink_last_team_from_group_fails(self, auth_client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)
        resp = await auth_client.get("/groups/")
        resp = await auth_client.get(f"/groups/{group_id}")
        team_id = resp.json()["teams"][0]["id"]

        resp = await auth_client.post(f"/groups/{group_id}/teams/{team_id}/unlink", json={"encrypted_private_key": "fake_key"})
        assert resp.status_code == 400  # Cannot remove last team

    async def test_unlink_group_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.post("/groups/99999/teams/1/unlink", json={"encrypted_private_key": "fake_key"})
        assert resp.status_code in (403, 404)


@pytest.mark.asyncio
class TestDeviceInstall:
    async def test_get_install_script_linux(self, client: AsyncClient):
        resp = await client.get("/device/install?os=linux")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        script = resp.text
        assert "#!/bin/bash" in script
        assert "RADEGAST_TOKEN" in script
        assert "%REPLACE_WITH_YOUR_AGENT_TOKEN%" in script
        assert "radegast-agent" in script
        assert "rustinel" in script

    async def test_get_install_script_windows(self, client: AsyncClient):
        resp = await client.get("/device/install?os=windows")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        script = resp.text
        assert "@echo off" in script
        assert "Radegast EDR Installation" in script
        assert "Decoding script" in script

    async def test_get_install_script_invalid_os(self, client: AsyncClient):
        resp = await client.get("/device/install?os=macos")
        assert resp.status_code == 400

    async def test_download_agent_latest(self, client: AsyncClient):
        resp = await client.get("/device/agent/download?os=linux&arch=amd64")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert len(resp.content) > 0

    async def test_download_agent_not_found(self, client: AsyncClient):
        resp = await client.get("/device/agent/download?os=linux&arch=nonexistent")
        assert resp.status_code == 404

    async def test_get_install_script_custom_agent_package_linux(self, client: AsyncClient):
        old_package = settings.agent_package
        settings.agent_package = "custom-agent-package-linux-test"
        try:
            resp = await client.get("/device/install?os=linux")
            assert resp.status_code == 200
            script = resp.text
            assert "tool install --upgrade custom-agent-package-linux-test" in script
            assert "tool install --upgrade radegast-edr-agent" not in script
        finally:
            settings.agent_package = old_package

    async def test_get_install_script_custom_agent_package_windows(self, client: AsyncClient):
        old_package = settings.agent_package
        settings.agent_package = "custom-agent-package-windows-test"
        try:
            resp = await client.get("/device/install?os=windows")
            assert resp.status_code == 200
            script = resp.text
            chunks = re.findall(r"\(echo\s+([A-Za-z0-9+/=]+)\)", script)
            decoded_service = base64.b64decode("".join(chunks)).decode("utf-8")
            assert '"tool", "install", "--upgrade", "--force", "custom-agent-package-windows-test"' in decoded_service
            assert '"tool", "install", "--upgrade", "--force", "radegast-edr-agent"' not in decoded_service
        finally:
            settings.agent_package = old_package


@pytest.mark.asyncio
class TestDeviceReinstall:
    async def test_reinstall_device(self, auth_client: AsyncClient, client: AsyncClient):
        group_id = await _get_default_group_id(auth_client)

        # 1. Create device
        resp = await auth_client.post("/devices/", json={"name": "Reinstall-01", "group_id": group_id})
        assert resp.status_code == 200
        device_id = resp.json()["id"]
        old_token = resp.json()["token"]

        # 2. Login with old token - works
        device_client = AsyncClient(transport=client._transport, base_url="http://test")
        resp = await device_client.post("/auth/device/login", json={"token": old_token})
        assert resp.status_code == 200
        assert "radegast_session" in resp.cookies

        # 3. Call reinstall endpoint (needs user auth, so use auth_client)
        resp = await auth_client.post(f"/devices/{device_id}/reinstall")
        assert resp.status_code == 200
        new_token = resp.json()["token"]
        assert new_token != old_token

        # 4. Old session should be invalidated (since token_change was set)
        resp = await device_client.post("/devices/signing-key", json={"signature_public_key": "test-key"})
        assert resp.status_code == 401  # Session invalidated

        # 5. Login with new token - works
        resp = await device_client.post("/auth/device/login", json={"token": new_token})
        assert resp.status_code == 200

    async def test_reinstall_nonexistent_device(self, auth_client: AsyncClient):
        resp = await auth_client.post("/devices/99999/reinstall")
        assert resp.status_code == 404

    async def test_reinstall_unauthenticated(self, client: AsyncClient):
        resp = await client.post("/devices/1/reinstall")
        assert resp.status_code == 401
