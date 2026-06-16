from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.queued_email import QueuedEmail
from app.services.email import (
    process_email_queue,
    send_login_notification,
    send_verification_email,
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
            queued[0].created_at = datetime.now(tz=UTC) - timedelta(minutes=4)
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
            queued[0].created_at = datetime.now(tz=UTC) - timedelta(minutes=4)
            await db_session.commit()

            # Run process_email_queue - should send all 3 in bulk as one email
            await process_email_queue()

            mock_direct.assert_called_once()
            to_email, subject, html_body, *_rest = mock_direct.call_args[0]
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
                q.created_at = datetime.now(tz=UTC) - timedelta(minutes=4)
            await db_session.commit()

            await process_email_queue()

            # Should result in 2 separate calls to send_email_direct
            assert mock_direct.call_count == 2
            calls = [c[0][0] for c in mock_direct.call_args_list]
            assert "user1@example.com" in calls
            assert "user2@example.com" in calls
    finally:
        settings.email_debounce_seconds = original_debounce


@pytest.mark.asyncio
async def test_email_bulking_progression_headers_and_limit(db_session):
    from app.models.email_bulk_state import EmailBulkState

    original_intervals = settings.email_bulk_intervals
    original_reset = settings.email_bulk_reset_hours
    # We can use the default settings (3, 3, 6, 16, 37, 62, 122, 193) and 24 hours
    try:
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            intervals = [3, 3, 6, 16, 37, 62, 122, 193]

            # We will send 8 batches and verify the interval/headers progression
            for i, interval in enumerate(intervals):
                # Queue one notification
                await send_login_notification("test_bulk@example.com", f"10.0.0.{i}")

                # Fetch it to modify created_at
                result = await db_session.execute(select(QueuedEmail))
                queued = result.scalars().all()
                assert len(queued) == 1

                # Make it expire the current debounce limit (interval + 1 minute)
                queued[0].created_at = datetime.now(tz=UTC) - timedelta(minutes=interval + 1)
                await db_session.commit()

                # Process the queue
                mock_direct.reset_mock()
                await process_email_queue()

                # Check it was sent
                mock_direct.assert_called_once()
                to_email, subject, html_body, *_rest = mock_direct.call_args[0]
                assert to_email == "test_bulk@example.com"
                assert "[Bulk]" not in subject
                assert f"10.0.0.{i}" in html_body
                # Check next interval text in the body
                if i + 1 < len(intervals):
                    next_int = intervals[i + 1]
                else:
                    next_int = intervals[-1]

                if next_int > 10:
                    assert "This email contains 1 bulk events." in html_body
                    assert f"The next bulk email will arrive in {next_int} minutes if more events occur." in html_body
                else:
                    assert "This email contains 1 bulk events." not in html_body
                    assert f"The next bulk email will arrive in {next_int} minutes if more events occur." not in html_body

                # Verify queue is empty after sending
                result = await db_session.execute(select(QueuedEmail))
                assert len(result.scalars().all()) == 0

                # Verify state in database
                db_session.expire_all()
                result_state = await db_session.execute(
                    select(EmailBulkState).where(EmailBulkState.email_to == "test_bulk@example.com", EmailBulkState.email_type == "login")
                )
                state = result_state.scalar_one()
                assert state.sent_count == i + 1
                assert state.last_sent_at is not None

            # Now we are at state.sent_count = 8 (which is past the 8 predefined intervals).
            # Queue 9th notification - it should be sent after the last interval (193 minutes)
            await send_login_notification("test_bulk@example.com", "10.0.0.8")
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            assert len(queued) == 1

            # Make it look expired for 193 minutes (194 minutes ago)
            queued[0].created_at = datetime.now(tz=UTC) - timedelta(minutes=194)
            await db_session.commit()

            mock_direct.reset_mock()
            await process_email_queue()

            # Ensure send_email_direct was called and the email was sent
            mock_direct.assert_called_once()
            to_email, subject, html_body, *_rest = mock_direct.call_args[0]
            assert "10.0.0.8" in html_body
            assert "The next bulk email will arrive in 193 minutes if more events occur." in html_body

            result = await db_session.execute(select(QueuedEmail))
            assert len(result.scalars().all()) == 0

            # Verify sent_count is now 9
            db_session.expire_all()
            result_state = await db_session.execute(
                select(EmailBulkState).where(EmailBulkState.email_to == "test_bulk@example.com", EmailBulkState.email_type == "login")
            )
            state = result_state.scalar_one()
            assert state.sent_count == 9

            # Queue 10th notification to test the reset logic
            await send_login_notification("test_bulk@example.com", "10.0.0.9")
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            assert len(queued) == 1

            # Manually set the state's last_sent_at to 25 hours ago, and queued email to 4 minutes ago
            state.last_sent_at = datetime.now(tz=UTC) - timedelta(hours=25)
            queued[0].created_at = datetime.now(tz=UTC) - timedelta(minutes=4)
            await db_session.commit()

            # Process the queue - it should reset the sequence (sent_count becomes 0)
            # and send the email with the first interval (3 minutes)
            mock_direct.reset_mock()
            await process_email_queue()

            mock_direct.assert_called_once()
            to_email, subject, html_body, *_rest = mock_direct.call_args[0]
            assert "10.0.0.9" in html_body
            assert "The next bulk email will arrive in 3 minutes if more events occur." not in html_body

            # Check queue is empty and state reset correctly
            result = await db_session.execute(select(QueuedEmail))
            assert len(result.scalars().all()) == 0

            db_session.expire_all()
            result_state = await db_session.execute(
                select(EmailBulkState).where(EmailBulkState.email_to == "test_bulk@example.com", EmailBulkState.email_type == "login")
            )
            state = result_state.scalar_one()
            assert state.sent_count == 1

    finally:
        settings.email_bulk_intervals = original_intervals
        settings.email_bulk_reset_hours = original_reset


@pytest.mark.asyncio
async def test_bulk_subject_prefix_depends_on_count(db_session):
    original_debounce = settings.email_debounce_seconds
    settings.email_debounce_seconds = 180

    try:
        # 1. Single email in queue -> no [Bulk] prefix
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            await send_login_notification("single@example.com", "192.168.1.1")
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            assert len(queued) == 1
            queued[0].created_at = datetime.now(tz=UTC) - timedelta(minutes=4)
            await db_session.commit()

            await process_email_queue()
            mock_direct.assert_called_once()
            to_email, subject, _html_body, *_rest = mock_direct.call_args[0]
            assert to_email == "single@example.com"
            assert "[Bulk]" not in subject

        # 2. Multiple emails in queue -> [Bulk] prefix
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            await send_login_notification("multi@example.com", "192.168.1.1")
            await send_login_notification("multi@example.com", "192.168.1.2")
            result = await db_session.execute(select(QueuedEmail))
            queued = result.scalars().all()
            assert len(queued) == 2
            for q in queued:
                q.created_at = datetime.now(tz=UTC) - timedelta(minutes=4)
            await db_session.commit()

            await process_email_queue()
            mock_direct.assert_called_once()
            to_email, subject, _html_body, *_rest = mock_direct.call_args[0]
            assert to_email == "multi@example.com"
            assert subject.startswith("[Bulk]")

    finally:
        settings.email_debounce_seconds = original_debounce
