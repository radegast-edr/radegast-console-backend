"""Tests that email notifications are sent (or suppressed) when security events occur."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from app.models.user import User
from app.services.auth import create_signed_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register_and_verify(client: AsyncClient, email: str, password: str):
    await client.post("/auth/register", json={"email": email, "password": password})
    token = create_signed_token({"email": email}, salt="email-verify")
    await client.get(f"/auth/verify?token={token}")


async def _login(client: AsyncClient, email: str, password: str):
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp


async def _set_notify_flag(db_engine, email: str, **flags):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        for attr, val in flags.items():
            setattr(user, attr, val)
        await session.commit()


async def _setup_keys(client: AsyncClient):
    """Submit a minimal key-setup payload (no real AGE crypto needed for notification tests)."""
    return await client.post(
        "/auth/keys/setup",
        json={
            "public_key": "age1pub",
            "recovery_public_key": "age1rec",
            "recovery_encrypted_private_key": "enc-priv"
        },
    )


# ---------------------------------------------------------------------------
# Login notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestLoginNotification:
    async def test_login_sends_notification_when_enabled(self, client: AsyncClient):
        email = "login_notif_on@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await _login(client, email, password)

        # At least one call should be for the login alert
        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("Login Alert" in s for s in subjects)

    async def test_login_skips_notification_when_disabled(
        self, client: AsyncClient, db_engine
    ):
        email = "login_notif_off@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _set_notify_flag(db_engine, email, notify_login=False)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await _login(client, email, password)

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert not any("Login Alert" in s for s in subjects)

    async def test_login_notification_includes_ip(self, client: AsyncClient):
        email = "login_notif_ip@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await _login(client, email, password)

        # The HTML body (3rd arg) should mention an IP address field
        bodies = [call.args[2] for call in mock_send.call_args_list]
        login_bodies = [b for b in bodies if "Login" in b]
        assert login_bodies
        assert "IP address" in login_bodies[0]


# ---------------------------------------------------------------------------
# New keys notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestNewKeysNotification:
    async def test_setup_primary_key_sends_notification(self, client: AsyncClient):
        email = "newkeys_on@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await _setup_keys(client)

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("New Encryption Keys" in s for s in subjects)

    async def test_setup_secondary_key_sends_notification(self, client: AsyncClient):
        email = "seckey_on@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)
        await _setup_keys(client)  # primary first

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.post(
                "/auth/keys/secondary",
                json={"public_key": "age1sec", "encrypted_private_key": "enc-sec"},
            )

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("New Encryption Keys" in s for s in subjects)

    async def test_setup_key_skips_notification_when_disabled(
        self, client: AsyncClient, db_engine
    ):
        email = "newkeys_off@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _set_notify_flag(db_engine, email, notify_new_keys=False)
        await _login(client, email, password)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await _setup_keys(client)

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert not any("New Encryption Keys" in s for s in subjects)

    async def test_new_keys_notification_includes_ip(self, client: AsyncClient):
        email = "newkeys_ip@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await _setup_keys(client)

        bodies = [call.args[2] for call in mock_send.call_args_list]
        key_bodies = [b for b in bodies if "Encryption Keys" in b]
        assert key_bodies
        assert "IP address" in key_bodies[0]


# ---------------------------------------------------------------------------
# Recovery key notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRecoveryNotification:
    async def test_recovery_sends_notification_when_enabled(self, client: AsyncClient):
        email = "recovery_on@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)
        await _setup_keys(client)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.get("/auth/keys/recover")

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("Recovery Key Used" in s for s in subjects)

    async def test_recovery_skips_notification_when_disabled(
        self, client: AsyncClient, db_engine
    ):
        email = "recovery_off@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _set_notify_flag(db_engine, email, notify_recovery_used=False)
        await _login(client, email, password)
        await _setup_keys(client)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.get("/auth/keys/recover")

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert not any("Recovery Key Used" in s for s in subjects)

    async def test_recovery_notification_includes_ip(self, client: AsyncClient):
        email = "recovery_ip@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)
        await _setup_keys(client)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.get("/auth/keys/recover")

        bodies = [call.args[2] for call in mock_send.call_args_list]
        rec_bodies = [b for b in bodies if "Recovery Key" in b]
        assert rec_bodies
        assert "IP address" in rec_bodies[0]


# ---------------------------------------------------------------------------
# Key transfer notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestTransferNotification:
    async def _setup_transfer(self, client: AsyncClient, email: str, password: str):
        await _register_and_verify(client, email, password)
        await _login(client, email, password)
        await _setup_keys(client)
        resp = await client.post(
            "/auth/keys/transfer/initiate",
            json={"receiver_age_public_key": "age1recv"},
        )
        return resp.json()["transfer_id"]

    async def test_transfer_complete_sends_notification(self, client: AsyncClient):
        transfer_id = await self._setup_transfer(
            client, "transfer_on@example.com", "Password123!"
        )

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.post(
                f"/auth/keys/transfer/{transfer_id}/complete",
                json={"encrypted_private_key": "enc-for-receiver"},
            )

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("Keys Transferred" in s for s in subjects)

    async def test_transfer_skips_notification_when_disabled(
        self, client: AsyncClient, db_engine
    ):
        email = "transfer_off@example.com"
        transfer_id = await self._setup_transfer(client, email, "Password123!")
        await _set_notify_flag(db_engine, email, notify_keys_transferred=False)

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.post(
                f"/auth/keys/transfer/{transfer_id}/complete",
                json={"encrypted_private_key": "enc-for-receiver"},
            )

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert not any("Keys Transferred" in s for s in subjects)

    async def test_transfer_notification_includes_ip(self, client: AsyncClient):
        transfer_id = await self._setup_transfer(
            client, "transfer_ip@example.com", "Password123!"
        )

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.post(
                f"/auth/keys/transfer/{transfer_id}/complete",
                json={"encrypted_private_key": "enc-for-receiver"},
            )

        bodies = [call.args[2] for call in mock_send.call_args_list]
        xfer_bodies = [b for b in bodies if "Transferred" in b]
        assert xfer_bodies
        assert "IP address" in xfer_bodies[0]


# ---------------------------------------------------------------------------
# Device log notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDeviceLogNotification:
    async def _setup(self, client: AsyncClient, db_engine, email: str, notify: bool):
        """Register user, enable/disable log notification, create device, return device token."""
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _set_notify_flag(db_engine, email, notify_device_log=notify)
        await _login(client, email, password)

        resp = await client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await client.post("/devices/", json={"name": "NotifDevice", "group_id": group_id})
        return resp.json()["token"]

    async def test_device_log_sends_notification_when_enabled(
        self, client: AsyncClient, db_engine
    ):
        token = await self._setup(client, db_engine, "devlog_on@example.com", notify=True)

        device_client = client  # reuse — we'll log in as device separately
        await device_client.post("/auth/device/login", json={"token": token})

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await device_client.post(
                "/logs/",
                json={"time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "content": "enc-log"},
            )

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("New Alert" in s for s in subjects)

    async def test_device_log_skips_notification_when_disabled(
        self, client: AsyncClient, db_engine
    ):
        token = await self._setup(client, db_engine, "devlog_off@example.com", notify=False)
        await client.post("/auth/device/login", json={"token": token})

        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await client.post(
                "/logs/",
                json={"time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "content": "enc-log"},
            )

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert not any("New Alert" in s for s in subjects)


@pytest.mark.asyncio
class TestNotificationDisabledAlert:
    async def test_notification_disabled_sends_email(self, client: AsyncClient):
        email = "notif_disabled_test@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)

        payload = {
            "notify_login": False,
            "notify_new_keys": True,
            "notify_recovery_used": True,
            "notify_keys_transferred": True,
            "notify_device_log": True,
            "notify_downtime_maintenance": True,
        }
        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            resp = await client.put("/auth/notifications", json=payload)
            assert resp.status_code == 200

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert any("Notification Settings Disabled" in s for s in subjects)

    async def test_notification_no_change_skips_email(self, client: AsyncClient):
        email = "notif_disabled_no_change@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)

        payload = {
            "notify_login": True,
            "notify_new_keys": True,
            "notify_recovery_used": True,
            "notify_keys_transferred": True,
            "notify_device_log": True,
            "notify_downtime_maintenance": True,
        }
        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            resp = await client.put("/auth/notifications", json=payload)
            assert resp.status_code == 200

        subjects = [call.args[1] for call in mock_send.call_args_list]
        assert not any("Notification Settings Disabled" in s for s in subjects)

    async def test_notification_downtime_maintenance_disabled_sends_email(self, client: AsyncClient):
        email = "notif_disabled_downtime@example.com"
        password = "Password123!"
        await _register_and_verify(client, email, password)
        await _login(client, email, password)

        payload = {
            "notify_login": True,
            "notify_new_keys": True,
            "notify_recovery_used": True,
            "notify_keys_transferred": True,
            "notify_device_log": True,
            "notify_downtime_maintenance": False,
        }
        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            resp = await client.put("/auth/notifications", json=payload)
            assert resp.status_code == 200

        calls = mock_send.call_args_list
        assert len(calls) > 0
        disabled_emails = [call.args for call in calls if "Notification Settings Disabled" in call.args[1]]
        assert len(disabled_emails) == 1
        body = disabled_emails[0][2]
        assert "Platform downtime and maintenance emails" in body
