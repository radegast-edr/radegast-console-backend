import pytest
from datetime import datetime, timezone as tz, timedelta
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models.user import User
from app.services.email import send_email_direct, send_verification_email
from app.services.auth import create_signed_token
from app.routers.auth import _configured_webauthn_origins
from app.utils import utc_now


@pytest.mark.asyncio
async def test_unsubscribe_link_appended_to_registered_user_email(db_session):
    # Setup user
    email = "unsub_target@example.com"
    user = User(email=email, password="hashedpassword", verified=True)
    db_session.add(user)
    await db_session.commit()

    orig_host = settings.smtp_host
    settings.smtp_host = "smtp.example.com"
    try:
        # Call send_email_direct (mocking SMTP) with email_type="login"
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email_direct(email, "Test Subject", "<html><body>Hello!</body></html>", email_type="login")

        # Verify aiosmtplib.send was called
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        html_body = msg.get_payload()

        # The HTML should contain unsubscribe link and mention the specific preference
        assert "unsubscribe" in html_body
        assert "token=" in html_body
        assert "new login alerts" in html_body.lower()
    finally:
        settings.smtp_host = orig_host


@pytest.mark.asyncio
async def test_unsubscribe_link_not_appended_to_transactional_email(db_session):
    # Setup user
    email = "unsub_transactional@example.com"
    user = User(email=email, password="hashedpassword", verified=True)
    db_session.add(user)
    await db_session.commit()

    orig_host = settings.smtp_host
    settings.smtp_host = "smtp.example.com"
    try:
        # Call send_email_direct with transactional type or None
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email_direct(email, "Verify", "<html><body>Hello!</body></html>", email_type="verify")

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        html_body = msg.get_payload()

        # The HTML should NOT contain unsubscribe link since it's transactional
        assert "unsubscribe" not in html_body
    finally:
        settings.smtp_host = orig_host


@pytest.mark.asyncio
async def test_unsubscribe_link_not_appended_to_unregistered_email(db_session):
    email = "nonexistent_unregistered@example.com"
    orig_host = settings.smtp_host
    settings.smtp_host = "smtp.example.com"
    try:
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email_direct(email, "Test Subject", "<html><body>Hello!</body></html>", email_type="login")

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        html_body = msg.get_payload()

        # The HTML should NOT contain unsubscribe link since user doesn't exist
        assert "unsubscribe" not in html_body
    finally:
        settings.smtp_host = orig_host


@pytest.mark.asyncio
async def test_unsubscribe_api_success(client: AsyncClient, db_session):
    # Setup user
    email = "unsub_api@example.com"
    user = User(email=email, password="hashedpassword", verified=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # All notifications are True by default
    assert user.notify_login is True
    assert user.notify_new_keys is True

    # Generate unsubscribe token for only "notify_login" (login alerts)
    expires_at = (utc_now() + timedelta(weeks=2)).isoformat()
    token = create_signed_token({
        "user_id": user.id,
        "expires_at": expires_at,
        "preference_field": "notify_login"
    }, salt="unsubscribe")

    # Post to unsubscribe endpoint
    resp = await client.post("/auth/unsubscribe", json={"token": token})
    assert resp.status_code == 200
    assert "Successfully unsubscribed from new login alerts" in resp.json()["message"]
    assert resp.json()["preference_name"] == "New login alerts"

    # Re-fetch user and verify ONLY notify_login is False, others are still True
    await db_session.refresh(user)
    assert user.notify_login is False
    assert user.notify_new_keys is True
    assert user.notify_recovery_used is True
    assert user.notify_keys_transferred is True
    assert user.notify_device_log is True
    assert user.notify_downtime_maintenance is True


@pytest.mark.asyncio
async def test_unsubscribe_api_expired_token(client: AsyncClient, db_session):
    email = "unsub_expired@example.com"
    user = User(email=email, password="hashedpassword", verified=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Token expires 1 second in the past
    expires_at = (utc_now() - timedelta(seconds=1)).isoformat()
    token = create_signed_token({
        "user_id": user.id,
        "expires_at": expires_at,
        "preference_field": "notify_login"
    }, salt="unsubscribe")

    resp = await client.post("/auth/unsubscribe", json={"token": token})
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()
    assert "log in" in resp.json()["detail"].lower()
    assert "manually" in resp.json()["detail"].lower()

    # Preferences should remain True
    await db_session.refresh(user)
    assert user.notify_login is True


@pytest.mark.asyncio
async def test_unsubscribe_api_invalid_token(client: AsyncClient):
    resp = await client.post("/auth/unsubscribe", json={"token": "invalidtoken123"})
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_web_ui_url_config_and_origins(db_session):
    # Save original settings
    orig_web_ui_url = settings.web_ui_url
    try:
        settings.web_ui_url = "https://custom-ui.radegast.app"
        
        # Test WebAuthn origins lists custom-ui.radegast.app
        origins = _configured_webauthn_origins()
        assert "https://custom-ui.radegast.app" in origins

        # Test verification email link has custom web UI URL
        with patch("app.services.email.send_email", new_callable=AsyncMock) as mock_send:
            await send_verification_email("verify_config@example.com")
        
        mock_send.assert_called_once()
        html_body = mock_send.call_args[0][2]
        assert "https://custom-ui.radegast.app/verify" in html_body

    finally:
        settings.web_ui_url = orig_web_ui_url
