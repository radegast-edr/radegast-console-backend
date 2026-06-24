import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


@pytest.mark.asyncio
class TestAPIKeys:
    async def test_api_keys_disabled_by_default(self, auth_client: AsyncClient):
        # 1. Listing should fail when API keys are disabled
        resp = await auth_client.get("/apikeys/")
        assert resp.status_code == 403
        assert "API keys support is disabled" in resp.json()["detail"]

        # 2. Creating should fail when API keys are disabled
        resp = await auth_client.post(
            "/apikeys/", json={"name": "My Key", "scopes": {"devices": ["read"], "teams": [], "groups": [], "packs": [], "logs": []}}
        )
        assert resp.status_code == 403

    async def test_api_keys_crud_when_enabled(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Enable API keys for the user in the database
        result = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        user.api_keys_enabled = True
        await db_session.commit()

        # 1. Create API key
        resp = await auth_client.post(
            "/apikeys/",
            json={
                "name": "My New Key",
                "scopes": {"devices": ["read"], "teams": ["read", "create", "write", "delete"], "groups": [], "packs": [], "logs": []},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My New Key"
        assert "key" in data
        assert data["key"].startswith("rg_")
        data["key"]
        key_id = data["id"]

        # 2. List API keys
        resp = await auth_client.get("/apikeys/")
        assert resp.status_code == 200
        keys_list = resp.json()
        assert len(keys_list) == 1
        assert keys_list[0]["id"] == key_id
        assert keys_list[0]["name"] == "My New Key"
        # The raw key value should NOT be in the list response
        assert "key" not in keys_list[0]

        # 3. Delete API key
        resp = await auth_client.delete(f"/apikeys/{key_id}")
        assert resp.status_code == 200

        # 4. List should be empty now
        resp = await auth_client.get("/apikeys/")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_api_keys_access_control(self, client: AsyncClient, auth_client: AsyncClient, db_session: AsyncSession):
        # Enable API keys
        result = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        user.api_keys_enabled = True
        await db_session.commit()

        # Create API key with read-only devices access, none for teams
        resp = await auth_client.post(
            "/apikeys/",
            json={"name": "Restricted Key", "scopes": {"devices": ["read"], "teams": [], "groups": [], "packs": [], "logs": []}},
        )
        assert resp.status_code == 200
        raw_key = resp.json()["key"]

        # Check that last_used is initially None
        list_resp = await auth_client.get("/apikeys/")
        assert list_resp.status_code == 200
        assert list_resp.json()[0]["last_used"] is None

        # Use new unauthenticated client with Authorization header
        # Test 1: Accessing devices (read) should work
        headers = {"Authorization": f"Bearer {raw_key}"}
        resp = await client.get("/devices/", headers=headers)
        assert resp.status_code == 200

        # Check that last_used is updated
        list_resp = await auth_client.get("/apikeys/")
        assert list_resp.status_code == 200
        assert list_resp.json()[0]["last_used"] is not None

        # Test 2: Modifying devices (write) should fail with 403
        resp = await client.post("/devices/", json={"name": "New Dev", "group_id": 1}, headers=headers)
        assert resp.status_code == 403
        assert "API key does not have 'create' permission for scope 'devices'" in resp.json()["detail"]

        # Test 3: Accessing teams (none) should fail with 403
        resp = await client.get("/teams/", headers=headers)
        assert resp.status_code == 403
        assert "API key does not have 'read' permission for scope 'teams'" in resp.json()["detail"]

        # Test 4: Disable API keys support via settings endpoint
        resp = await auth_client.put("/user/api-keys-enabled", json={"api_keys_enabled": False})
        assert resp.status_code == 200
        assert resp.json()["api_keys_enabled"] is False

        # Now calling with the key should fail
        resp = await client.get("/devices/", headers=headers)
        assert resp.status_code == 401

    async def test_api_key_creation_sends_notification(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Enable API keys for the user
        result = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        user.api_keys_enabled = True
        user.notify_api_key_modification = True
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            resp = await auth_client.post(
                "/apikeys/",
                json={
                    "name": "Notify Key",
                    "scopes": {"devices": ["read"], "teams": ["read", "create", "write", "delete"], "groups": [], "packs": [], "logs": []},
                },
            )
            assert resp.status_code == 200

        # Assert email was sent
        calls = mock_send.call_args_list
        assert len(calls) == 1
        to_email, subject, html_body = calls[0].args[:3]
        assert to_email == "test@example.com"
        assert "New API Key Created: Notify Key" in subject
        assert "Devices:</strong> read" in html_body
        assert "Teams:</strong> read, create, write, delete" in html_body
        assert "Groups" not in html_body

    async def test_api_key_creation_skips_notification_when_disabled(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Enable API keys but disable notifications for user
        result = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        user.api_keys_enabled = True
        user.notify_api_key_modification = False
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            resp = await auth_client.post(
                "/apikeys/",
                json={
                    "name": "Silent Key",
                    "scopes": {"devices": ["read"], "teams": ["read", "create", "write", "delete"], "groups": [], "packs": [], "logs": []},
                },
            )
            assert resp.status_code == 200

        # Assert no email was sent
        assert len(mock_send.call_args_list) == 0

    async def test_api_keys_toggled_sends_notification(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Reset preferences
        result = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        user.api_keys_enabled = False
        user.notify_api_key_modification = True
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            # 1. Enable
            resp = await auth_client.put("/user/api-keys-enabled", json={"api_keys_enabled": True})
            assert resp.status_code == 200

        # Assert email was sent
        calls = mock_send.call_args_list
        assert len(calls) == 1
        to_email, subject, html_body = calls[0].args[:3]
        assert to_email == "test@example.com"
        assert "API Keys Support Enabled" in subject
        assert "enabled" in html_body

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            # 2. Disable
            resp = await auth_client.put("/user/api-keys-enabled", json={"api_keys_enabled": False})
            assert resp.status_code == 200

        # Assert email was sent
        calls = mock_send.call_args_list
        assert len(calls) == 1
        to_email, subject, html_body = calls[0].args[:3]
        assert to_email == "test@example.com"
        assert "API Keys Support Disabled" in subject
        assert "disabled" in html_body

    async def test_api_keys_toggled_skips_notification_when_disabled(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Reset preferences with notifications disabled
        result = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        user.api_keys_enabled = False
        user.notify_api_key_modification = False
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            resp = await auth_client.put("/user/api-keys-enabled", json={"api_keys_enabled": True})
            assert resp.status_code == 200

        # Assert no email was sent
        assert len(mock_send.call_args_list) == 0
