import aiosmtplib
from datetime import datetime
from email.mime.text import MIMEText
from jinja2 import Template

from app.config import settings
from app.services.auth import create_signed_token

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
<h2>New Alert — Radegast EDR</h2>
<p>A new alert was submitted by device <strong>{{ device_name }}</strong> (ID: {{ device_id }}).</p>
<p><strong>Time:</strong> {{ time }} UTC</p>
<p><a href="{{ base_url }}/ui/alerts">View Alerts</a></p>
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


async def send_email(to: str, subject: str, html_body: str):
    msg = MIMEText(html_body, "html")
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject

    if not settings.smtp_host or settings.smtp_host == "localhost":
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
        start_tls=True,
    )


async def send_verification_email(email: str):
    token = create_signed_token({"email": email}, salt="email-verify")
    url = f"{settings.base_url}/ui/verify?token={token}"
    html = VERIFY_EMAIL_TEMPLATE.render(url=url)
    await send_email(email, "Verify your Radegast EDR account", html)


async def send_invite_email(email: str, team_id: int, team_name: str):
    token = create_signed_token({"email": email, "team_id": team_id}, salt="team-invite")
    url = f"{settings.base_url}/auth/invite/accept?token={token}"
    html = INVITE_EMAIL_TEMPLATE.render(url=url, team_name=team_name)
    await send_email(email, f"Invitation to join {team_name}", html)


async def send_login_notification(email: str, ip: str):
    html = LOGIN_ALERT_TEMPLATE.render(
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "New Login Alert — Radegast EDR", html)


async def send_new_keys_notification(email: str, key_type: str = "primary", ip: str = "unknown"):
    html = NEW_KEYS_TEMPLATE.render(
        key_type=key_type,
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "New Encryption Keys Added — Radegast EDR", html)


async def send_recovery_used_notification(email: str, ip: str):
    html = RECOVERY_USED_TEMPLATE.render(
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "Recovery Key Used — Radegast EDR", html)


async def send_keys_transferred_notification(email: str, ip: str):
    html = KEYS_TRANSFERRED_TEMPLATE.render(
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        ip=ip,
    )
    await send_email(email, "Encryption Keys Transferred — Radegast EDR", html)


async def send_device_log_notification(email: str, device_name: str, device_id: int):
    html = DEVICE_LOG_TEMPLATE.render(
        device_name=device_name,
        device_id=device_id,
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        base_url=settings.base_url,
    )
    await send_email(email, f"New Alert from {device_name} — Radegast EDR", html)


async def send_notification_disabled_alert(email: str, disabled_features: list[str]):
    html = NOTIFICATION_DISABLED_TEMPLATE.render(
        features=disabled_features,
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await send_email(email, "Notification Settings Disabled — Radegast EDR", html)
