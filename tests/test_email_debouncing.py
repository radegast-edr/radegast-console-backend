import pytest
from datetime import datetime, timezone as tz, timedelta
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from app.config import settings
from app.models.queued_email import QueuedEmail
from app.services.email import (
    send_verification_email,
    send_login_notification,
    process_email_queue,
)


@pytest.mark.asyncio
async def test_verification_email_sent_immediately(db_session):
    # Verification emails should bypass the queue and call send_email_direct
    with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
        await send_verification_email("verify@example.com")

    # Check that it did not get queued in the database
    result = await db_session.execute(select(QueuedEmail))
    queued = result.scalars().all()
    assert len(queued) == 0

    # Check that send_email_direct was called
    mock_direct.assert_called_once()
    assert mock_direct.call_args[0][0] == "verify@example.com"
    assert "Verify your Radegast EDR account" in mock_direct.call_args[0][1]


@pytest.mark.asyncio
async def test_debounce_email_queued_and_sent_after_timeout(db_session):
    # Set email debounce to 180 seconds for this test
    original_debounce = settings.email_debounce_seconds
    settings.email_debounce_seconds = 180

    try:
        # Send a login alert
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            await send_login_notification("user@example.com", "127.0.0.1")

            # Verify it is queued in the database
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            assert len(queued) == 1
            assert queued[0].email_to == "user@example.com"
            assert queued[0].email_type == "login"
            assert "New Login Alert" in queued[0].subject

            # Run process_email_queue immediately - should NOT send (as debounce is 180s)
            await process_email_queue()
            mock_direct.assert_not_called()

            # Verify it is still queued
            result = await db_session.execute(select(QueuedEmail))
            assert len(result.scalars().all()) == 1

            # Manually alter the created_at to be 4 minutes ago to simulate passage of time
            queued[0].created_at = datetime.now(tz=tz.utc) - timedelta(minutes=4)
            await db_session.commit()

            # Run process_email_queue again - should send now
            await process_email_queue()
            mock_direct.assert_called_once()
            assert mock_direct.call_args[0][0] == "user@example.com"
            assert "New Login Alert" in mock_direct.call_args[0][1]

            # Verify it is deleted from the queue
            result = await db_session.execute(select(QueuedEmail))
            assert len(result.scalars().all()) == 0
    finally:
        settings.email_debounce_seconds = original_debounce


@pytest.mark.asyncio
async def test_bulk_email_grouping_and_combining(db_session):
    original_debounce = settings.email_debounce_seconds
    settings.email_debounce_seconds = 180

    try:
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            # Queue multiple login notifications for the same user
            await send_login_notification("bulk@example.com", "192.168.1.1")
            await send_login_notification("bulk@example.com", "192.168.1.2")
            await send_login_notification("bulk@example.com", "192.168.1.3")

            # Verify 3 emails are in the queue
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            assert len(queued) == 3

            # Make the oldest one (index 0) expire the debounce period
            queued[0].created_at = datetime.now(tz=tz.utc) - timedelta(minutes=4)
            await db_session.commit()

            # Run process_email_queue - should send all 3 in bulk as one email
            await process_email_queue()

            mock_direct.assert_called_once()
            to_email, subject, html_body = mock_direct.call_args[0]
            assert to_email == "bulk@example.com"
            assert subject.startswith("[Bulk]")
            assert "New Login Alert" in subject
            assert "192.168.1.1" in html_body
            assert "192.168.1.2" in html_body
            assert "192.168.1.3" in html_body
            assert '<hr style="border: 0; border-top: 1px solid #ccc;' in html_body

            # Verify queue is now empty
            result = await db_session.execute(select(QueuedEmail))
            assert len(result.scalars().all()) == 0
    finally:
        settings.email_debounce_seconds = original_debounce


@pytest.mark.asyncio
async def test_different_users_or_types_not_combined(db_session):
    original_debounce = settings.email_debounce_seconds
    settings.email_debounce_seconds = 180

    try:
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            # Different users, same type
            await send_login_notification("user1@example.com", "127.0.0.1")
            await send_login_notification("user2@example.com", "127.0.0.1")

            # Make both expire
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            for q in queued:
                q.created_at = datetime.now(tz=tz.utc) - timedelta(minutes=4)
            await db_session.commit()

            await process_email_queue()

            # Should result in 2 separate calls to send_email_direct
            assert mock_direct.call_count == 2
            calls = [c[0][0] for c in mock_direct.call_args_list]
            assert "user1@example.com" in calls
            assert "user2@example.com" in calls
    finally:
        settings.email_debounce_seconds = original_debounce
