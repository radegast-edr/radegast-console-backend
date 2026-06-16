from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.queued_email import QueuedEmail
from app.models.user import User


class TestAdminBroadcasts:
    @pytest.mark.asyncio
    async def test_send_broadcast_as_admin(self, admin_client: AsyncClient, db_session):
        # Count current users in database who are subscribed
        result = await db_session.execute(select(User).where(User.notify_downtime_maintenance))
        users_count = len(result.scalars().all())

        # Post admin broadcast
        resp = await admin_client.post(
            "/admin/broadcast",
            json={
                "subject": "Platform Maintenance Announcement",
                "html_body": "<p>System is undergoing maintenance.</p>",
                "email_type": "downtime_maintenance",
            },
        )
        assert resp.status_code == 200
        assert "Successfully queued broadcast" in resp.json()["message"]

        # Verify queued emails in database
        result_queued = await db_session.execute(select(QueuedEmail).where(QueuedEmail.subject == "Platform Maintenance Announcement"))
        queued_emails = result_queued.scalars().all()
        assert len(queued_emails) == users_count

        # Spacing validation
        for i, qe in enumerate(sorted(queued_emails, key=lambda q: q.id)):
            wave_index = i // 20
            # scheduled_at should be ~ now - 200 min + wave_index min
            expected_diff = timedelta(minutes=wave_index)
            # check the difference between the first queued email scheduled_at and this one
            diff = qe.scheduled_at - queued_emails[0].scheduled_at
            assert abs(diff.total_seconds() - expected_diff.total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_send_broadcast_as_regular_user(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/admin/broadcast",
            json={
                "subject": "Maintenance announcement",
                "html_body": "<p>Content</p>",
                "email_type": "downtime_maintenance",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_broadcast_unsubscribed_users_excluded(self, admin_client: AsyncClient, db_session):
        # Create users
        user1 = User(email="sub_news@example.com", password="password123", verified=True, notify_news_updates=True)  # noqa: S106
        user2 = User(email="unsub_news@example.com", password="password123", verified=True, notify_news_updates=False)  # noqa: S106
        db_session.add(user1)
        db_session.add(user2)
        await db_session.commit()

        resp = await admin_client.post(
            "/admin/broadcast",
            json={
                "subject": "Platform News 2026",
                "html_body": "<p>News content</p>",
                "email_type": "news_updates",
            },
        )
        assert resp.status_code == 200

        # Verify unsubscribed user did not get queued
        result_queued = await db_session.execute(select(QueuedEmail).where(QueuedEmail.subject == "Platform News 2026"))
        queued_emails = result_queued.scalars().all()
        emails = {q.email_to for q in queued_emails}

        assert "sub_news@example.com" in emails
        assert "unsub_news@example.com" not in emails

        # Cleanup
        for q in queued_emails:
            await db_session.delete(q)
        await db_session.delete(user1)
        await db_session.delete(user2)
        await db_session.commit()
