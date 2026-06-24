import asyncio
import random
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import aiosmtplib
from filelock import Timeout
from jinja2 import Template
from sqlalchemy import delete, select

import app.database
from app.config import settings
from app.models.email_bulk_state import EmailBulkState
from app.models.log import LogSeverity
from app.models.queued_email import QueuedEmail
from app.models.user import User
from app.services.auth import create_signed_token
from app.utils import ensure_utc, get_worker_lock, utc_now

VERIFY_EMAIL_TEMPLATE = Template("""
<html>
<body>
<h2>Verify your Radegast EDR account</h2>
<p>Click the link below to verify your email address:</p>
<p><a href="{{ url }}">Verify Email</a></p>
<p>If you did not register, please ignore this email.</p>
</body>
</html>
""")

INVITE_EMAIL_TEMPLATE = Template("""
<html>
<body>
<h2>Team Invitation</h2>
<p>You have been invited to join the team <strong>{{ team_name }}</strong> on Radegast EDR.</p>
<p><a href="{{ url }}">Accept Invitation</a></p>
</body>
</html>
""")

LOGIN_ALERT_TEMPLATE = Template("""
<html>
<body>
<h2>New Login — Radegast EDR</h2>
<p>A new login to your account was detected.</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p><strong>IP address:</strong> {{ ip }}</p>
<p>If this was not you, please change your password immediately.</p>
</body>
</html>
""")

NEW_KEYS_TEMPLATE = Template("""
<html>
<body>
<h2>New Encryption Keys Added — Radegast EDR</h2>
<p>A new <strong>{{ key_type }}</strong> encryption key was added to your account.</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p><strong>IP address:</strong> {{ ip }}</p>
<p>If this was not you, please contact your administrator immediately.</p>
</body>
</html>
""")

RECOVERY_USED_TEMPLATE = Template("""
<html>
<body>
<h2>Recovery Key Used — Radegast EDR</h2>
<p>Your account's encrypted private key was retrieved using the recovery endpoint.</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p><strong>IP address:</strong> {{ ip }}</p>
<p>If this was not you, your recovery key may be compromised. Please re-generate your keys.</p>
</body>
</html>
""")

KEYS_TRANSFERRED_TEMPLATE = Template("""
<html>
<body>
<h2>Encryption Keys Transferred — Radegast EDR</h2>
<p>Your private key was transferred to another device or browser session.</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p><strong>IP address:</strong> {{ ip }}</p>
<p>If this was not you, please revoke your keys immediately.</p>
</body>
</html>
""")

DEVICE_LOG_TEMPLATE = Template("""
<html>
<body>
<h2>New Alert{% if severity %} [{{ severity|upper }}]{% endif %} — Radegast EDR</h2>
<p>A new alert was submitted by device <strong>{{ device_name }}</strong> (ID: {{ device_id }}).{% if severity %} Severity: <strong>{{ severity }}</strong>.{% endif %}</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p><a href="{{ base_url }}/alerts{% if log_id %}#focused_alert={{ log_id }}{% endif %}{% if from_time %}&from={{ from_time }}{% endif %}{% if to_time %}&to={{ to_time }}{% endif %}">View This Alert</a></p>
</body>
</html>
""")

SEVERITY_CHANGED_TEMPLATE = Template("""
<html>
<body>
<h2>Notification Severity Level Changed — Radegast EDR</h2>
<p>Your alert notification severity preference has been changed from <strong>{{ old_level }}</strong> to <strong>{{ new_level }}</strong>.</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p>If you did not make this change, please review your account settings immediately.</p>
</body>
</html>
""")

NOTIFICATION_DISABLED_TEMPLATE = Template("""
<html>
<body>
<h2>Notification Disabled — Radegast EDR</h2>
<p>One or more email notifications were disabled in your user settings:</p>
<ul>
    {% for feature in features %}
    <li>{{ feature }}</li>
    {% endfor %}
</ul>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p>If you did not make this change, please review your account settings immediately.</p>
</body>
</html>
""")

API_KEY_CREATED_TEMPLATE = Template("""
<html>
<body>
<h2>New API Key Created — Radegast EDR</h2>
<p>A new API key has been created for your account.</p>
<p><strong>Name:</strong> {{ name }}</p>
<p><strong>Allowed Scopes:</strong></p>
<ul>
    {% for scope, val in scopes.items() %}
    <li><strong>{{ scope|capitalize }}:</strong> {{ val }}</li>
    {% endfor %}
</ul>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p>If you did not generate this key, please revoke it immediately and review your account security.</p>
</body>
</html>
""")

API_KEYS_TOGGLED_TEMPLATE = Template("""
<html>
<body>
<h2>API Keys Support Preferences Updated — Radegast EDR</h2>
<p>API keys support has been <strong>{{ status }}</strong> for your account.</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p>If you did not make this change, please review your account settings immediately.</p>
</body>
</html>
""")


def get_web_ui_base() -> str:
    if settings.web_ui_url:
        return settings.web_ui_url.rstrip("/")
    return f"{settings.base_url.rstrip('/')}/ui"


EMAIL_TYPE_TO_PREFERENCE = {
    "login": ("notify_login", "New login alerts"),
    "new_keys": ("notify_new_keys", "New encryption keys added"),
    "recovery": ("notify_recovery_used", "Recovery key usage alerts"),
    "keys_transferred": (
        "notify_keys_transferred",
        "Encryption keys transferred alerts",
    ),
    "device_log": ("notify_device_log", "New device alerts"),
    "downtime_maintenance": (
        "notify_downtime_maintenance",
        "Platform downtime and maintenance emails",
    ),
    "api_key_modification": (
        "notify_api_key_modification",
        "API key modification alerts",
    ),
    "news_updates": (
        "notify_news_updates",
        "Platform news and updates",
    ),
}


async def send_email_direct(to: str, subject: str, html_body: str, email_type: str | None = None):
    async with app.database.async_session() as session:
        result = await session.execute(select(User).where(User.email == to))
        user = result.scalar_one_or_none()

    api_unsubscribe_url = None
    if user and email_type in EMAIL_TYPE_TO_PREFERENCE:
        pref_field, pref_name = EMAIL_TYPE_TO_PREFERENCE[email_type]
        expires_at = (utc_now() + timedelta(weeks=2)).isoformat()
        token = create_signed_token(
            {
                "user_id": user.id,
                "expires_at": expires_at,
                "preference_field": pref_field,
            },
            salt="unsubscribe",
        )
        ui_base = get_web_ui_base()
        unsubscribe_url = f"{ui_base}/unsubscribe?token={token}"
        api_unsubscribe_url = f"{settings.base_url.rstrip('/')}/user/unsubscribe?token={token}"

        footer_html = f"""<div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; font-size: 12px; color: #666; text-align: center;">
    If you no longer wish to receive {pref_name.lower()}, you can
    <a href="{unsubscribe_url}" style="color: #0066cc; text-decoration: none;">unsubscribe here</a>.
</div>"""
        if "</body>" in html_body:
            html_body = html_body.replace("</body>", f"{footer_html}</body>")
        elif "</BODY>" in html_body:
            html_body = html_body.replace("</BODY>", f"{footer_html}</BODY>")
        else:
            html_body += footer_html

    msg = MIMEText(html_body, "html")
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject

    if api_unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{api_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if not settings.smtp_host:
        # In development, just log the email
        print(f"[EMAIL] To: {to}, Subject: {subject}")
        print(f"[EMAIL] Body: {html_body}")
        return

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_starttls,
    )


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    email_type: str | None = None,
    scheduled_at: datetime | None = None,
):
    # Verification emails are sent right away.
    if email_type == "verify" or email_type is None:
        await send_email_direct(to, subject, html_body, email_type)
        return

    # Queue the email
    async with app.database.async_session() as session:
        queued_email = QueuedEmail(
            email_to=to,
            email_type=email_type,
            subject=subject,
            html_body=html_body,
            created_at=scheduled_at or utc_now(),
            scheduled_at=scheduled_at or utc_now(),
        )
        session.add(queued_email)
        await session.commit()


async def send_verification_email(email: str):
    token = create_signed_token({"email": email}, salt="email-verify")
    ui_base = get_web_ui_base()
    url = f"{ui_base}/verify?token={token}"
    html = VERIFY_EMAIL_TEMPLATE.render(url=url)
    await send_email(email, "Verify your Radegast EDR account", html, email_type="verify")


async def send_invite_email(email: str, team_id: int, team_name: str):
    token = create_signed_token({"email": email, "team_id": team_id}, salt="team-invite")
    ui_base = get_web_ui_base()
    url = f"{ui_base}/invite/accept?token={token}"
    html = INVITE_EMAIL_TEMPLATE.render(url=url, team_name=team_name)
    await send_email(email, f"Invitation to join {team_name}", html, email_type="invite")


async def send_login_notification(email: str, ip: str):
    html = LOGIN_ALERT_TEMPLATE.render(
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "New Login Alert — Radegast EDR", html, email_type="login")


async def send_new_keys_notification(email: str, key_type: str = "primary", ip: str = "unknown"):
    html = NEW_KEYS_TEMPLATE.render(
        key_type=key_type,
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "New Encryption Keys Added — Radegast EDR", html, email_type="new_keys")


async def send_recovery_used_notification(email: str, ip: str):
    html = RECOVERY_USED_TEMPLATE.render(
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "Recovery Key Used — Radegast EDR", html, email_type="recovery")


async def send_keys_transferred_notification(email: str, ip: str):
    html = KEYS_TRANSFERRED_TEMPLATE.render(
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(
        email,
        "Encryption Keys Transferred — Radegast EDR",
        html,
        email_type="keys_transferred",
    )


async def send_device_log_notification(
    email: str, device_name: str, device_id: int, severity: str | None = None, log_id: int | None = None, alert_time: datetime | None = None
):
    ui_base = get_web_ui_base()

    # Format time range filters if alert_time is provided
    from_time = None
    to_time = None
    if alert_time:
        # Create a time range around the alert (+/- 5 minutes)
        time_format = "%Y-%m-%dT%H:%M"
        from_time = (alert_time - timedelta(minutes=5)).strftime(time_format)
        to_time = (alert_time + timedelta(minutes=5)).strftime(time_format)

    html = DEVICE_LOG_TEMPLATE.render(
        device_name=device_name,
        device_id=device_id,
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        base_url=ui_base,
        severity=severity,
        log_id=log_id,
        from_time=from_time,
        to_time=to_time,
    )
    subject = (
        f"New Alert [{severity.upper()}] from {device_name} — Radegast EDR" if severity else f"New Alert from {device_name} — Radegast EDR"
    )
    await send_email(email, subject, html, email_type="device_log")


async def send_severity_changed_email(email: str, old_level: LogSeverity, new_level: LogSeverity):
    html = SEVERITY_CHANGED_TEMPLATE.render(
        old_level=old_level.value,
        new_level=new_level.value,
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await send_email(
        email,
        "Notification Severity Preference Changed — Radegast EDR",
        html,
        email_type="severity_changed",
    )


async def send_notification_disabled_alert(email: str, disabled_features: list[str]):
    html = NOTIFICATION_DISABLED_TEMPLATE.render(
        features=disabled_features,
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await send_email(
        email,
        "Notification Settings Disabled — Radegast EDR",
        html,
        email_type="notification_disabled",
    )


async def send_api_key_created_notification(email: str, name: str, scopes: dict):
    # Format scopes for display
    formatted_scopes = {}
    for scope, val in scopes.items():
        if isinstance(val, list):
            if val:
                formatted_scopes[scope] = ", ".join(val)
        elif val and val != "none":
            formatted_scopes[scope] = val

    html = API_KEY_CREATED_TEMPLATE.render(
        name=name,
        scopes=formatted_scopes,
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await send_email(
        email,
        f"New API Key Created: {name} — Radegast EDR",
        html,
        email_type="api_key_modification",
    )


async def send_api_keys_toggled_notification(email: str, enabled: bool):
    status_str = "enabled" if enabled else "disabled"
    html = API_KEYS_TOGGLED_TEMPLATE.render(
        status=status_str,
        time=utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await send_email(
        email,
        f"API Keys Support {status_str.capitalize()} — Radegast EDR",
        html,
        email_type="api_key_modification",
    )


async def send_password_reset_email(email: str, new_password: str):
    html = f"""
    <h2>Password Reset</h2>
    <p>Your Radegast EDR password has been reset by an administrator.</p>
    <p>Your new temporary password is: <pre>{new_password}</pre></p>
    <p><strong>Change your new password as soon as possible</strong></p>
    <p>All MFA devices (OTP, Hardware tokens) have been removed from your account. You can configure them again in your account settings after logging in.</p>
    """
    await send_email(email, "Your Radegast EDR password has been reset", html, email_type="verify")


def combine_html_bodies(html_bodies: list[str]) -> str:
    import re

    extracted_bodies = []
    for html in html_bodies:
        match = re.search(r"<body>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
        if match:
            extracted_bodies.append(match.group(1).strip())
        else:
            extracted_bodies.append(html.strip())

    divider = '<hr style="border: 0; border-top: 1px solid #ccc; margin: 30px 0;" />'
    combined_content = divider.join(extracted_bodies)

    return f"""<html>
<body style="font-family: sans-serif; padding: 20px; line-height: 1.6; color: #333;">
{combined_content}
</body>
</html>"""


async def process_email_queue():
    intervals = [int(x.strip()) for x in settings.email_bulk_intervals.split(",")]
    now = utc_now()

    async with app.database.async_session() as session:
        result = await session.execute(select(QueuedEmail).where(QueuedEmail.scheduled_at <= now).order_by(QueuedEmail.scheduled_at.asc()))
        queued_emails = result.scalars().all()

        if not queued_emails:
            return

        groups = {}
        for qe in queued_emails:
            key = (qe.email_to, qe.email_type)
            if key not in groups:
                groups[key] = []
            groups[key].append(qe)

        for (email_to, email_type), emails in groups.items():
            result_state = await session.execute(
                select(EmailBulkState).where(
                    EmailBulkState.email_to == email_to,
                    EmailBulkState.email_type == email_type,
                )
            )
            state = result_state.scalar_one_or_none()
            if not state:
                state = EmailBulkState(
                    email_to=email_to,
                    email_type=email_type,
                    last_sent_at=None,
                    sent_count=0,
                )
                session.add(state)

            oldest_email = emails[0]
            oldest_created = oldest_email.created_at
            oldest_created = ensure_utc(oldest_created)

            if state.last_sent_at is not None:
                last_sent_utc = ensure_utc(state.last_sent_at)
                reset_delta = timedelta(hours=settings.email_bulk_reset_hours)
                if oldest_created - last_sent_utc > reset_delta:
                    state.sent_count = 0
                    state.last_sent_at = None

            if state.sent_count < len(intervals):
                debounce_minutes = intervals[state.sent_count]
            else:
                debounce_minutes = intervals[-1]
            debounce_limit = timedelta(minutes=debounce_minutes)

            if now - oldest_created >= debounce_limit:
                event_count = len(emails)

                next_index = state.sent_count + 1
                if next_index < len(intervals):
                    next_interval = intervals[next_index]
                else:
                    next_interval = intervals[-1]
                next_email_text = f"The next bulk email will arrive in {next_interval} minutes if more events occur."

                if event_count > 1 or next_interval > 10:
                    header_html = f"""
    <div style="background-color: #f4f5f7; border-left: 4px solid #0052cc; padding: 12px; margin-bottom: 20px; font-family: sans-serif; font-size: 14px; line-height: 1.5; color: #333;">
        <strong>Bulk Notification Summary</strong><br/>
        This email contains {event_count} bulk events.<br/>
        {next_email_text}
    </div>
    """
                else:
                    # Show header only if multiple bulk emails or next email in longer time
                    header_html = ""

                if event_count > 1:
                    subject = f"[Bulk] {emails[0].subject}"
                else:
                    subject = emails[0].subject
                html_bodies = [e.html_body for e in emails]
                combined_body = combine_html_bodies(html_bodies)

                body_tag = '<body style="font-family: sans-serif; padding: 20px; line-height: 1.6; color: #333;">'
                if body_tag in combined_body:
                    combined_body = combined_body.replace(body_tag, f"{body_tag}{header_html}")
                elif "<body>" in combined_body:
                    combined_body = combined_body.replace("<body>", f"<body>{header_html}")
                else:
                    combined_body = header_html + combined_body

                await send_email_direct(email_to, subject, combined_body, email_type)

                state.sent_count += 1
                state.last_sent_at = now

                ids_to_delete = [e.id for e in emails]
                await session.execute(delete(QueuedEmail).where(QueuedEmail.id.in_(ids_to_delete)))
                await session.commit()


async def process_email_queue_loop():
    lock = get_worker_lock()
    while True:
        try:
            async with lock:
                await process_email_queue()
        except Timeout:
            pass
        except Exception as e:
            print(f"[EMAIL QUEUE WORKER ERROR] {e}")
        await asyncio.sleep(random.randint(5, 60))
