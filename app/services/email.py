import aiosmtplib
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
    url = f"{settings.base_url}/auth/verify?token={token}"
    html = VERIFY_EMAIL_TEMPLATE.render(url=url)
    await send_email(email, "Verify your Radegast EDR account", html)


async def send_invite_email(email: str, team_id: int, team_name: str):
    token = create_signed_token({"email": email, "team_id": team_id}, salt="team-invite")
    url = f"{settings.base_url}/auth/invite/accept?token={token}"
    html = INVITE_EMAIL_TEMPLATE.render(url=url, team_name=team_name)
    await send_email(email, f"Invitation to join {team_name}", html)
