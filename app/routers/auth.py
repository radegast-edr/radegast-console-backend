import base64
import json
import secrets
import string
from datetime import timedelta
from urllib.parse import urlparse

import httpx
import pyotp
import webauthn
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

from app.config import settings
from app.database import get_db
from app.dependencies import (
    check_rate_limit,
    mfa_level_value,
    rate_limit_login,
    rate_limit_mfa,
    rate_limit_token,
)
from app.middleware.session import create_session_cookie
from app.models.associations import team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.hardware_token import HardwareToken
from app.models.public_key import PublicKey
from app.models.team import Team
from app.models.user import User
from app.schemas.device import DeviceLogin
from app.schemas.user import (
    MfaHardwareTokenAssertionOptionsRequest,
    MfaHardwareTokenAssertionOptionsResponse,
    MfaVerifyRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services.auth import (
    create_signed_token,
    hash_password,
    hash_token,
    verify_password,
    verify_signed_token,
)
from app.services.email import (
    send_login_notification,
    send_password_reset_link_email,
    send_user_password_reset_email,
    send_verification_email,
)
from app.utils import ensure_utc, utc_now

SECURE_COOKIE = settings.environment != "dev"

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Return the real client IP, checking CF-Connecting-IP, X-Real-IP, and X-Forwarded-For first."""
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _normalize_origin(origin: str) -> str | None:
    origin = origin.strip().rstrip("/")
    if not origin:
        return None
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.port:
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}"


def _configured_webauthn_origins() -> list[str]:
    origins: list[str] = []
    for source in (
        settings.base_url,
        settings.cors_origins,
        settings.webauthn_origins,
        settings.web_ui_url,
    ):
        if not source:
            continue
        parts = source.split(",") if isinstance(source, str) else []
        for candidate in parts:
            normalized = _normalize_origin(candidate)
            if normalized and normalized not in origins:
                origins.append(normalized)
    return origins


def _resolve_webauthn_rp_id(request: Request | None = None) -> str:
    configured = (settings.webauthn_rp_id or "").strip()
    if configured:
        return configured

    allowed_origins = _configured_webauthn_origins()
    request_origin = _normalize_origin((request.headers.get("origin") if request else "") or "")
    if request_origin and request_origin in allowed_origins:
        parsed = urlparse(request_origin)
        if parsed.hostname:
            return parsed.hostname

    parsed = urlparse(settings.base_url)
    return parsed.hostname or "localhost"


async def _verify_turnstile(token: str | None, remote_ip: str) -> None:
    if not settings.turnstile_secret_key or not settings.turnstile_site_key:
        return
    if not token:
        raise HTTPException(status_code=400, detail="Turnstile verification token is required")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": settings.turnstile_secret_key,
                    "response": token,
                    "remoteip": remote_ip,
                },
                timeout=5.0,
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to verify Turnstile token with Cloudflare",
                )
            result = resp.json()
            if not result.get("success"):
                raise HTTPException(status_code=400, detail="Turnstile verification failed")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Turnstile verification request failed: {e!s}") from e


@router.post("/register", response_model=UserResponse)
async def register(data: UserRegister, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _client_ip(request)
    await _verify_turnstile(data.turnstile_token, ip)
    existing: User | None = (await db.execute(select(User).where(User.email == data.email))).scalar_one_or_none()
    if existing:
        if not existing.verified:
            now = utc_now()
            if now - ensure_utc(existing.password_change) > timedelta(hours=24):
                await send_verification_email(data.email)
                existing.password_change = now
                await db.commit()
                raise HTTPException(
                    status_code=400,
                    detail="Email not verified. A new verification email has been sent.",
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Email not verified. Please check your inbox.",
                )

        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=data.email,
        password=hash_password(data.password),
        verified=False,
    )
    db.add(user)
    await db.flush()

    # Create default team
    team = Team(
        name=f"{data.email}'s team",
        permission_pack="write",
        permission_invite="write",
        permission_admin="write",
        permission_logs="read",
    )
    db.add(team)
    await db.flush()

    await db.execute(insert(team_users).values(team_id=team.id, user_id=user.id))

    # Create default device group
    group = DeviceGroup(name=f"{data.email}'s device group")
    db.add(group)
    await db.flush()

    await db.execute(insert(team_device_groups).values(team_id=team.id, device_group_id=group.id))

    await db.commit()
    await db.refresh(user)

    await send_verification_email(data.email)

    return user


@router.get("/verify")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    data = verify_signed_token(token, salt="email-verify", max_age=86400)
    if not data or "email" not in data:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    result = await db.execute(select(User).where(User.email == data["email"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.verified = True
    await db.commit()

    return {"message": "Email verified successfully"}


@router.post("/token")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(rate_limit_token),
):
    result = await db.execute(select(User).options(selectinload(User.hardware_tokens)).where(User.email == form_data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    # Check if they have MFA configured
    has_otp = user.otp_enabled and user.otp_secret is not None
    has_token = len(user.hardware_tokens) > 0

    if has_otp or has_token:
        raise HTTPException(
            status_code=403,
            detail="MFA is required for this user. "
            "OAuth2 password login in OpenAPI docs is only supported for users "
            "without MFA. Please use API key authentication instead.",
        )

    # Generate a standard signed session token
    token = create_session_cookie("user", user.id, mfa_level="none")
    return {
        "access_token": token,
        "token_type": "bearer",
    }


@router.post("/login")
async def login(
    data: UserLogin,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(rate_limit_login),
):
    result = await db.execute(select(User).options(selectinload(User.hardware_tokens)).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    if data.public_key:
        pk_res = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id, PublicKey.public_key == data.public_key))
        pk_obj = pk_res.scalar_one_or_none()
        if pk_obj:
            pk_obj.last_used_at = utc_now()

    # Check if they have MFA configured
    has_otp = user.otp_enabled and user.otp_secret is not None
    has_token = len(user.hardware_tokens) > 0

    if has_otp or has_token:
        # Determine the required MFA level for this user's role
        required_level = "none"
        if user.role.value == "admin":
            required_level = settings.mfa_required_level_admin
        elif user.role.value == "maintainer":
            required_level = settings.mfa_required_level_maintainer
        elif user.role.value == "user":
            required_level = settings.mfa_required_level_user

        methods = []
        # Only offer OTP if the required level does not mandate hardware_token
        if has_otp and mfa_level_value(required_level) < mfa_level_value("hardware_token"):
            methods.append("otp")
        if has_token:
            methods.append("hardware_token")

        mfa_token = create_signed_token({"user_id": user.id}, salt="mfa-login")
        await db.commit()
        return {
            "status": "mfa_required",
            "mfa_token": mfa_token,
            "methods": methods,
        }

    cookie = create_session_cookie("user", user.id, mfa_level="none")
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
        path="/",
    )

    if user.notify_login:
        background_tasks.add_task(send_login_notification, user.email, _client_ip(request))

    await db.commit()
    return {"message": "Login successful", "user_id": user.id}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"message": "Logged out"}


# Device auth
@router.post("/device/login")
async def device_login(data: DeviceLogin, response: Response, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(data.token)
    result = await db.execute(select(Device).where(Device.token == token_hash))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")

    cookie = create_session_cookie("device", device.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
        path="/",
    )

    return {"message": "Device login successful", "device_id": device.id}


# Invite acceptance
@router.get("/invite/accept")
async def accept_invite(token: str, db: AsyncSession = Depends(get_db)):
    data = verify_signed_token(token, salt="team-invite", max_age=86400 * 7)
    if not data or "email" not in data or "team_id" not in data:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")

    result = await db.execute(select(User).where(User.email == data["email"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")

    result = await db.execute(select(Team).options(selectinload(Team.users)).where(Team.id == data["team_id"]))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if user in team.users:
        return {"message": "Already a member of this team"}

    team.users.append(user)
    await db.commit()

    return {"message": f"Successfully joined team '{team.name}'"}


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def base64url_decode(data: str) -> bytes:
    rem = len(data) % 4
    if rem > 0:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data)


@router.post(
    "/mfa/hardware-token/assertion-options",
    response_model=MfaHardwareTokenAssertionOptionsResponse,
)
async def mfa_hardware_token_assertion_options(
    data: MfaHardwareTokenAssertionOptionsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token_data = verify_signed_token(data.mfa_token, salt="mfa-login", max_age=300)
    if not token_data or "user_id" not in token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired MFA token")

    result = await db.execute(select(HardwareToken).where(HardwareToken.user_id == token_data["user_id"]))
    tokens = result.scalars().all()
    if not tokens:
        raise HTTPException(status_code=400, detail="No Hardware tokens registered for this user")

    allow_credentials = [PublicKeyCredentialDescriptor(id=base64url_decode(t.credential_id)) for t in tokens]

    rp_id = _resolve_webauthn_rp_id(request)

    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials,
    )

    options_json = json.loads(webauthn.options_to_json(options))

    assertion_token = create_signed_token(
        {
            "challenge": options_json["challenge"],
            "user_id": token_data["user_id"],
            "rp_id": rp_id,
        },
        salt="hardware-token-login",
    )

    return MfaHardwareTokenAssertionOptionsResponse(options=options_json, assertion_token=assertion_token)


@router.post("/mfa/verify")
async def mfa_verify(
    data: MfaVerifyRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(rate_limit_mfa),
):
    token_data = verify_signed_token(data.mfa_token, salt="mfa-login", max_age=300)
    if not token_data or "user_id" not in token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired MFA token")

    user_id = token_data["user_id"]
    result = await db.execute(select(User).options(selectinload(User.hardware_tokens)).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.method == "otp":
        if not data.otp_code:
            raise HTTPException(status_code=400, detail="OTP code required")
        if not user.otp_secret or not user.otp_enabled:
            raise HTTPException(status_code=400, detail="OTP not enabled for this user")

        totp = pyotp.totp.TOTP(user.otp_secret)
        if not totp.verify(data.otp_code):
            raise HTTPException(status_code=400, detail="Invalid OTP code")

        mfa_level = "otp"

    elif data.method == "hardware_token":
        if not data.assertion_token or not data.webauthn_response:
            raise HTTPException(status_code=400, detail="Assertion token and webauthn response required")

        assert_data = verify_signed_token(data.assertion_token, salt="hardware-token-login", max_age=300)
        if not assert_data or assert_data.get("user_id") != user_id:
            raise HTTPException(status_code=400, detail="Invalid or expired assertion token")

        expected_challenge = base64url_decode(assert_data["challenge"])

        cred_id_str = data.webauthn_response.get("id")
        if not cred_id_str:
            raise HTTPException(status_code=400, detail="Credential ID missing from response")

        token_obj = next((k for k in user.hardware_tokens if k.credential_id == cred_id_str), None)
        if not token_obj:
            raise HTTPException(
                status_code=400,
                detail="Hardware token credential not registered for this user",
            )

        rp_id = str(assert_data.get("rp_id") or _resolve_webauthn_rp_id(request))
        origins = _configured_webauthn_origins()

        try:
            verification = webauthn.verify_authentication_response(
                credential=data.webauthn_response,
                expected_challenge=expected_challenge,
                expected_rp_id=rp_id,
                expected_origin=origins,
                credential_public_key=base64url_decode(token_obj.public_key),
                credential_current_sign_count=token_obj.sign_count,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"WebAuthn verification failed: {e!s}") from e

        token_obj.sign_count = verification.new_sign_count
        mfa_level = "hardware_token"

    else:
        raise HTTPException(status_code=400, detail="Unsupported MFA method")

    cookie = create_session_cookie("user", user.id, mfa_level=mfa_level)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
        path="/",
    )

    if user.notify_login:
        background_tasks.add_task(send_login_notification, user.email, _client_ip(request))

    await db.commit()
    return {"message": "Login successful", "user_id": user.id}


@router.get("/config")
async def get_auth_config():
    return {
        "turnstile_site_key": settings.turnstile_site_key,
    }


@router.post("/password-reset/request")
async def request_password_reset(
    data: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    await _verify_turnstile(data.turnstile_token, ip)

    # Rate limit: IP and Email
    check_rate_limit(request, f"password_reset_request:ip:{ip}", limit=1, window=180)
    check_rate_limit(request, f"password_reset_request:email:{data.email}", limit=1, window=180)

    # Prevent account enumeration by always returning the same response.
    # Find user by email
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if user and user.verified:
        await send_password_reset_link_email(user.email)

    return {"message": "If the account exists, a password reset link has been sent to your email."}


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    data: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    check_rate_limit(request, f"password_reset_confirm:ip:{ip}", limit=1, window=180)

    token_data = verify_signed_token(data.token, salt="password-reset", max_age=3600)
    if not token_data or "email" not in token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired password reset token")

    email = token_data["email"]
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    alphabet = string.ascii_letters + string.digits
    new_password = "".join(secrets.choice(alphabet) for _ in range(12))

    user.password = hash_password(new_password)
    user.password_change = utc_now()

    await db.commit()

    await send_user_password_reset_email(user.email, new_password)

    return {"message": "Your password has been reset successfully and the new password has been sent to your email."}
