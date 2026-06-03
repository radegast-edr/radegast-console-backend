import base64
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pyotp
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import webauthn
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

from app.config import settings
from app.database import get_db
from app.dependencies import (
    get_current_user,
    get_session,
    mfa_level_value,
    rate_limit_login,
    rate_limit_mfa,
    rate_limit_mfa_otp,
    user_has_required_mfa_setup,
)
from app.middleware.session import create_session_cookie
from app.models.associations import team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.hardware_token import HardwareToken
from app.models.key_transfer import KeyTransfer
from app.models.public_key import PublicKey
from app.models.team import Team
from app.models.user import User
from app.schemas.device import DeviceLogin
from app.schemas.user import (
    ChangePasswordRequest,
    KeyRecoverResponse,
    KeySecondarySetupRequest,
    KeySetupRequest,
    KeySetupResponse,
    KeyTransferCompleteRequest,
    KeyTransferInitiateRequest,
    KeyTransferInitiateResponse,
    KeyTransferStatusResponse,
    MfaHardwareTokenAssertionOptionsRequest,
    MfaHardwareTokenAssertionOptionsResponse,
    MfaHardwareTokenResponse,
    MfaHardwareTokenSetupResponse,
    MfaHardwareTokenVerifyRequest,
    MfaOtpSetupResponse,
    MfaOtpVerifyRequest,
    MfaSettingsResponse,
    MfaVerifyRequest,
    NotificationSettings,
    PublicKeyAddRequest,
    PublicKeyResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    UnsubscribeRequest,
)
from app.services.auth import (
    create_signed_token,
    hash_password,
    hash_token,
    verify_password,
    verify_signed_token,
    load_signed_token_without_age,
)
from app.services.email import (
    send_keys_transferred_notification,
    send_login_notification,
    send_new_keys_notification,
    send_notification_disabled_alert,
    send_recovery_used_notification,
    send_verification_email,
    EMAIL_TYPE_TO_PREFERENCE,
)
from app.utils import ensure_utc, utc_now

SECURE_COOKIE = True

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
    for source in (settings.base_url, settings.cors_origins, settings.webauthn_origins, settings.web_ui_url):
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

    import httpx
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
                raise HTTPException(status_code=400, detail="Failed to verify Turnstile token with Cloudflare")
            result = resp.json()
            if not result.get("success"):
                raise HTTPException(status_code=400, detail="Turnstile verification failed")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Turnstile verification request failed: {str(e)}")


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
                raise HTTPException(status_code=400, detail="Email not verified. A new verification email has been sent.")
            else:
                raise HTTPException(status_code=400, detail="Email not verified. Please check your inbox.")

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


@router.post("/login")
async def login(
    data: UserLogin,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _rate_limit = Depends(rate_limit_login),
):
    result = await db.execute(
        select(User).options(selectinload(User.hardware_tokens)).where(User.email == data.email)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    if data.public_key:
        pk_res = await db.execute(
            select(PublicKey).where(
                PublicKey.user_id == user.id, PublicKey.public_key == data.public_key
            )
        )
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
    )

    if user.notify_login:
        background_tasks.add_task(send_login_notification, user.email, _client_ip(request))

    await db.commit()
    return {"message": "Login successful", "user_id": user.id}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=settings.session_cookie_name)
    return {"message": "Logged out"}


@router.post("/keys/setup", response_model=KeySetupResponse)
async def setup_keys(
    data: KeySetupRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PublicKey).where(PublicKey.user_id == user.id)
    )
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Keys already set up")

    # Regular key (no private key on backend)
    key = PublicKey(
        public_key=data.public_key,
        private_key=None,
        key_type="regular",
        name=data.name or "Primary Key",
        user_id=user.id,
    )
    db.add(key)

    # Recovery key (private key encrypted on backend)
    rec_name = (data.name + " (Recovery)") if data.name else "Recovery Key"
    rec_key = PublicKey(
        public_key=data.recovery_public_key,
        private_key=data.recovery_encrypted_private_key,
        key_type="recovery",
        name=rec_name,
        user_id=user.id,
    )
    db.add(rec_key)

    await db.commit()

    if user.notify_new_keys:
        background_tasks.add_task(send_new_keys_notification, user.email, "primary", _client_ip(request))

    return KeySetupResponse(message="Keys set up successfully")


@router.post("/keys/secondary", response_model=KeySetupResponse)
async def setup_secondary_key(
    data: KeySecondarySetupRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a secondary key alongside the user's existing main keypair (e.g. on a new device)."""
    result = await db.execute(
        select(PublicKey).where(
            PublicKey.user_id == user.id, PublicKey.key_type == "regular"
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="No primary key found; use /keys/setup first")

    dup = await db.execute(
        select(PublicKey).where(
            PublicKey.user_id == user.id, PublicKey.key_type == "secondary"
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Secondary key already exists")

    key = PublicKey(
        public_key=data.public_key,
        private_key=data.encrypted_private_key,
        key_type="secondary",
        user_id=user.id,
    )
    db.add(key)
    await db.commit()

    if user.notify_new_keys:
        background_tasks.add_task(send_new_keys_notification, user.email, "secondary", _client_ip(request))

    return KeySetupResponse(message="Secondary key added successfully")


@router.delete("/keys")
async def delete_all_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all keys for the current user (fresh-start before re-setup)."""
    result = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id))
    keys = result.scalars().all()
    recovery_keys = [k for k in keys if k.private_key is not None]
    if len(recovery_keys) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete keys. At least one recovery key must always exist.")
    for key in keys:
        await db.delete(key)
    await db.commit()
    return {"message": "All keys deleted"}


@router.get("/keys", response_model=list[PublicKeyResponse])
async def list_user_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all public keys for the current user."""
    result = await db.execute(
        select(PublicKey).where(PublicKey.user_id == user.id)
    )
    keys = result.scalars().all()
    return [
        PublicKeyResponse(
            id=k.id,
            public_key=k.public_key,
            key_type="recovery" if k.private_key is not None else k.key_type,
            has_private_key=k.private_key is not None,
            name=k.name,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.post("/keys", response_model=KeySetupResponse)
async def add_user_key(
    data: PublicKeyAddRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a new public key for the current user."""
    # Check duplicate
    dup = await db.execute(
        select(PublicKey).where(
            PublicKey.user_id == user.id, PublicKey.public_key == data.public_key
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Public key already exists")

    key_type = "recovery" if data.encrypted_private_key is not None else data.key_type

    key = PublicKey(
        public_key=data.public_key,
        private_key=data.encrypted_private_key,
        key_type=key_type,
        name=data.name,
        user_id=user.id,
    )
    db.add(key)
    await db.commit()

    if user.notify_new_keys:
        background_tasks.add_task(send_new_keys_notification, user.email, key_type, _client_ip(request))

    return KeySetupResponse(message="Key added successfully")


@router.delete("/keys/{key_id}")
async def delete_user_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific public key belonging to the user."""
    result = await db.execute(
        select(PublicKey).where(PublicKey.user_id == user.id)
    )
    keys = result.scalars().all()
    key_to_delete = next((k for k in keys if k.id == key_id), None)
    if not key_to_delete:
        raise HTTPException(status_code=404, detail="Key not found")

    if key_to_delete.private_key is not None:
        recovery_keys = [k for k in keys if k.private_key is not None]
        if len(recovery_keys) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last recovery key. At least one recovery key must always exist.",
            )

    await db.delete(key_to_delete)
    await db.commit()
    return {"message": "Key deleted successfully"}


@router.get("/keys/recover", response_model=list[KeyRecoverResponse])
async def recover_keys(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PublicKey).where(
            PublicKey.user_id == user.id, PublicKey.private_key.isnot(None)
        )
    )
    keys = result.scalars().all()
    if not keys:
        raise HTTPException(status_code=404, detail="No recovery keys found")

    if user.notify_recovery_used:
        background_tasks.add_task(send_recovery_used_notification, user.email, _client_ip(request))

    return [
        KeyRecoverResponse(
            public_key=k.public_key,
            encrypted_private_key=k.private_key,
        )
        for k in keys
    ]


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    key_count = (
        await db.execute(
            select(func.count()).select_from(PublicKey).where(PublicKey.user_id == user.id)
        )
    ).scalar()

    required_level = "none"
    if user.role.value == "admin":
        required_level = settings.mfa_required_level_admin
    elif user.role.value == "maintainer":
        required_level = settings.mfa_required_level_maintainer
    elif user.role.value == "user":
        required_level = settings.mfa_required_level_user

    mfa_setup_missing = not user_has_required_mfa_setup(user, required_level)

    conf_level = "none"
    if len(user.hardware_tokens) > 0:
        conf_level = "hardware_token"
    elif user.otp_enabled and user.otp_secret:
        conf_level = "otp"

    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        verified=user.verified,
        has_keys=key_count > 0,
        mfa_required_level=required_level,
        mfa_setup_missing=mfa_setup_missing,
        mfa_configured_level=conf_level,
    )


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(data.old_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    user.password = hash_password(data.new_password)
    user.password_change = utc_now()
    await db.commit()
    return {"message": "Password changed successfully"}


@router.get("/notifications", response_model=NotificationSettings)
async def get_notifications(user: User = Depends(get_current_user)):
    return NotificationSettings.model_validate(user)


@router.put("/notifications", response_model=NotificationSettings)
async def update_notifications(
    data: NotificationSettings,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    disabled_features = []
    if user.notify_login and not data.notify_login:
        disabled_features.append("New login alert")
    if user.notify_new_keys and not data.notify_new_keys:
        disabled_features.append("New keys added")
    if user.notify_recovery_used and not data.notify_recovery_used:
        disabled_features.append("Recovery key used")
    if user.notify_keys_transferred and not data.notify_keys_transferred:
        disabled_features.append("Keys transferred to another device")
    if user.notify_device_log and not data.notify_device_log:
        disabled_features.append("New alert notification")
    if user.notify_downtime_maintenance and not data.notify_downtime_maintenance:
        disabled_features.append("Platform downtime and maintenance emails")

    user.notify_login = data.notify_login
    user.notify_new_keys = data.notify_new_keys
    user.notify_recovery_used = data.notify_recovery_used
    user.notify_keys_transferred = data.notify_keys_transferred
    user.notify_device_log = data.notify_device_log
    user.notify_downtime_maintenance = data.notify_downtime_maintenance

    severity_changed = False
    old_level = user.notification_level
    if old_level != data.notification_level:
        user.notification_level = data.notification_level
        severity_changed = True

    await db.commit()

    if disabled_features:
        await send_notification_disabled_alert(user.email, disabled_features)

    if severity_changed:
        from app.services.email import send_severity_changed_email
        await send_severity_changed_email(user.email, old_level, data.notification_level)

    return NotificationSettings.model_validate(user)


@router.post("/keys/transfer/initiate", response_model=KeyTransferInitiateResponse)
async def initiate_key_transfer(
    data: KeyTransferInitiateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    transfer = KeyTransfer(
        user_id=user.id,
        receiver_age_public_key=data.receiver_age_public_key,
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db.add(transfer)
    await db.commit()
    return KeyTransferInitiateResponse(transfer_id=transfer.id)


@router.get("/keys/transfer/{transfer_id}", response_model=KeyTransferStatusResponse)
async def get_key_transfer(
    transfer_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KeyTransfer).where(KeyTransfer.id == transfer_id)
    )
    transfer = result.scalar_one_or_none()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if transfer.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if utc_now() > ensure_utc(transfer.expires_at):
        raise HTTPException(status_code=410, detail="Transfer expired")

    return KeyTransferStatusResponse(
        transfer_id=transfer.id,
        status=transfer.status,
        receiver_age_public_key=transfer.receiver_age_public_key,
        encrypted_private_key=transfer.encrypted_private_key,
    )


@router.post("/keys/transfer/{transfer_id}/complete")
async def complete_key_transfer(
    transfer_id: str,
    data: KeyTransferCompleteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KeyTransfer).where(KeyTransfer.id == transfer_id)
    )
    transfer = result.scalar_one_or_none()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if transfer.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if utc_now() > ensure_utc(transfer.expires_at):
        raise HTTPException(status_code=410, detail="Transfer expired")
    if transfer.status == "completed":
        raise HTTPException(status_code=400, detail="Transfer already completed")

    transfer.encrypted_private_key = data.encrypted_private_key
    transfer.status = "completed"
    await db.commit()

    if user.notify_keys_transferred:
        background_tasks.add_task(send_keys_transferred_notification, user.email, _client_ip(request))

    return {"message": "Transfer completed"}


# Device auth
@router.post("/device/login")
async def device_login(
    data: DeviceLogin, response: Response, db: AsyncSession = Depends(get_db)
):
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

    result = await db.execute(
        select(Team).options(selectinload(Team.users)).where(Team.id == data["team_id"])
    )
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


@router.post("/mfa/otp/setup", response_model=MfaOtpSetupResponse)
async def mfa_otp_setup(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    secret = pyotp.random_base32()
    totp = pyotp.totp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name="Radegast EDR")

    user.otp_secret = secret
    user.otp_enabled = False
    await db.commit()

    return MfaOtpSetupResponse(secret=secret, provisioning_uri=uri)


@router.post("/mfa/otp/verify")
async def mfa_otp_verify(
    data: MfaOtpVerifyRequest,
    response: Response,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _rate_limit = Depends(rate_limit_mfa_otp),
):
    if not user.otp_secret:
        raise HTTPException(status_code=400, detail="OTP setup not initiated")

    totp = pyotp.totp.TOTP(user.otp_secret)
    if not totp.verify(data.code):
        raise HTTPException(status_code=400, detail="Invalid OTP code")

    user.otp_enabled = True
    await db.commit()

    cookie = create_session_cookie("user", user.id, mfa_level="otp")
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
    )

    return {"message": "OTP enabled successfully"}


@router.post("/mfa/hardware-token/setup", response_model=MfaHardwareTokenSetupResponse)
async def mfa_hardware_token_setup(
    request: Request,
    user: User = Depends(get_current_user),
):
    rp_id = _resolve_webauthn_rp_id(request)

    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name="Radegast EDR",
        user_id=str(user.id).encode("utf-8"),
        user_name=user.email,
        user_display_name=user.email,
    )

    options_json = json.loads(webauthn.options_to_json(options))

    registration_token = create_signed_token(
        {"challenge": options_json["challenge"], "user_id": user.id, "rp_id": rp_id},
        salt="hardware-token-register"
    )

    return MfaHardwareTokenSetupResponse(options=options_json, registration_token=registration_token)


@router.post("/mfa/hardware-token/verify")
async def mfa_hardware_token_verify(
    data: MfaHardwareTokenVerifyRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    token_data = verify_signed_token(data.registration_token, salt="hardware-token-register", max_age=300)
    if not token_data or token_data.get("user_id") != user.id:
        raise HTTPException(status_code=400, detail="Invalid or expired registration token")

    expected_challenge = base64url_decode(token_data["challenge"])

    rp_id = str(token_data.get("rp_id") or _resolve_webauthn_rp_id(request))
    origins = _configured_webauthn_origins()

    try:
        verification = webauthn.verify_registration_response(
            credential=data.credential_response,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=origins,
            require_user_verification=False,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"WebAuthn verification failed: {str(e)}")

    cred_id_str = base64url_encode(verification.credential_id)
    pub_key_str = base64url_encode(verification.credential_public_key)

    token = HardwareToken(
        user_id=user.id,
        credential_id=cred_id_str,
        public_key=pub_key_str,
        sign_count=verification.sign_count,
        name=data.name or "Hardware token",
    )
    db.add(token)
    await db.commit()

    cookie = create_session_cookie("user", user.id, mfa_level="hardware_token")
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
    )

    return {"message": "Hardware token registered successfully"}


@router.post("/mfa/hardware-token/assertion-options", response_model=MfaHardwareTokenAssertionOptionsResponse)
async def mfa_hardware_token_assertion_options(
    data: MfaHardwareTokenAssertionOptionsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token_data = verify_signed_token(data.mfa_token, salt="mfa-login", max_age=300)
    if not token_data or "user_id" not in token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired MFA token")

    result = await db.execute(
        select(HardwareToken).where(HardwareToken.user_id == token_data["user_id"])
    )
    tokens = result.scalars().all()
    if not tokens:
        raise HTTPException(status_code=400, detail="No Hardware tokens registered for this user")

    allow_credentials = [
        PublicKeyCredentialDescriptor(id=base64url_decode(t.credential_id))
        for t in tokens
    ]

    rp_id = _resolve_webauthn_rp_id(request)

    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials,
    )

    options_json = json.loads(webauthn.options_to_json(options))

    assertion_token = create_signed_token(
        {"challenge": options_json["challenge"], "user_id": token_data["user_id"], "rp_id": rp_id},
        salt="hardware-token-login"
    )

    return MfaHardwareTokenAssertionOptionsResponse(options=options_json, assertion_token=assertion_token)


@router.post("/mfa/verify")
async def mfa_verify(
    data: MfaVerifyRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _rate_limit = Depends(rate_limit_mfa),
):
    token_data = verify_signed_token(data.mfa_token, salt="mfa-login", max_age=300)
    if not token_data or "user_id" not in token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired MFA token")

    user_id = token_data["user_id"]
    result = await db.execute(
        select(User).options(selectinload(User.hardware_tokens)).where(User.id == user_id)
    )
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
            raise HTTPException(status_code=400, detail="Hardware token credential not registered for this user")

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
            raise HTTPException(status_code=400, detail=f"WebAuthn verification failed: {str(e)}")

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
    )

    if user.notify_login:
        background_tasks.add_task(send_login_notification, user.email, _client_ip(request))

    await db.commit()
    return {"message": "Login successful", "user_id": user.id}


@router.get("/mfa/settings", response_model=MfaSettingsResponse)
async def get_mfa_settings(
    request: Request,
    user: User = Depends(get_current_user),
    session = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HardwareToken).where(HardwareToken.user_id == user.id)
    )
    tokens = result.scalars().all()

    required_level = "none"
    if user.role.value == "admin":
        required_level = settings.mfa_required_level_admin
    elif user.role.value == "maintainer":
        required_level = settings.mfa_required_level_maintainer
    elif user.role.value == "user":
        required_level = settings.mfa_required_level_user

    current_level = getattr(session, "mfa_level", "none")

    return MfaSettingsResponse(
        otp_enabled=user.otp_enabled and user.otp_secret is not None,
        hardware_tokens=[MfaHardwareTokenResponse.model_validate(t) for t in tokens],
        required_level=required_level,
        current_level=current_level,
    )


@router.post("/mfa/otp/disable")
async def mfa_otp_disable(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HardwareToken).where(HardwareToken.user_id == user.id)
    )
    tokens = result.scalars().all()
    has_token = len(tokens) > 0

    required_level = "none"
    if user.role.value == "admin":
        required_level = settings.mfa_required_level_admin
    elif user.role.value == "maintainer":
        required_level = settings.mfa_required_level_maintainer
    elif user.role.value == "user":
        required_level = settings.mfa_required_level_user

    if required_level in ("otp", "hardware_token", "token") and not has_token:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot disable OTP. Your role requires at least '{required_level}' MFA, and you have no Hardware tokens registered."
        )

    user.otp_enabled = False
    user.otp_secret = None
    await db.commit()

    mfa_level = "hardware_token" if has_token else "none"
    cookie = create_session_cookie("user", user.id, mfa_level=mfa_level)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
    )

    return {"message": "OTP disabled successfully"}


@router.delete("/mfa/hardware-token/{token_id}")
async def delete_hardware_token(
    token_id: int,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HardwareToken).where(HardwareToken.user_id == user.id)
    )
    tokens = result.scalars().all()

    target_token = next((t for t in tokens if t.id == token_id), None)
    if not target_token:
        raise HTTPException(status_code=404, detail="Hardware token not found")

    required_level = "none"
    if user.role.value == "admin":
        required_level = settings.mfa_required_level_admin
    elif user.role.value == "maintainer":
        required_level = settings.mfa_required_level_maintainer
    elif user.role.value == "user":
        required_level = settings.mfa_required_level_user

    has_otp = user.otp_enabled and user.otp_secret is not None
    remaining_token_count = len(tokens) - 1

    if required_level in ("hardware_token", "token") and remaining_token_count == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete Hardware token. Your role requires Hardware token MFA, and this is your last registered Hardware token."
        )
    if required_level == "otp" and remaining_token_count == 0 and not has_otp:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete Hardware token. Your role requires at least OTP MFA, and you do not have OTP enabled."
        )

    await db.delete(target_token)
    await db.commit()

    mfa_level = "none"
    if remaining_token_count > 0:
        mfa_level = "hardware_token"
    elif has_otp:
        mfa_level = "otp"

    cookie = create_session_cookie("user", user.id, mfa_level=mfa_level)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=settings.session_max_age,
    )

    return {"message": "Hardware token deleted successfully"}


@router.get("/config")
async def get_auth_config():
    return {
        "turnstile_site_key": settings.turnstile_site_key,
    }


@router.post("/unsubscribe")
async def unsubscribe(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    token = request.query_params.get("token")
    if not token:
        try:
            body = await request.json()
            if isinstance(body, dict):
                token = body.get("token")
        except Exception:
            pass

    if not token:
        try:
            form = await request.form()
            token = form.get("token")
        except Exception:
            pass

    if not token:
        raise HTTPException(
            status_code=400,
            detail="Invalid unsubscribe link."
        )

    token_data = load_signed_token_without_age(token, salt="unsubscribe")
    if not token_data:
        raise HTTPException(
            status_code=400,
            detail="Invalid unsubscribe link."
        )

    user_id = token_data.get("user_id")
    expires_at_str = token_data.get("expires_at")
    if user_id is None or not expires_at_str:
        raise HTTPException(
            status_code=400,
            detail="Invalid unsubscribe link."
        )

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid unsubscribe link."
        )

    expires_at = ensure_utc(expires_at)
    if utc_now() > expires_at:
        raise HTTPException(
            status_code=400,
            detail="The unsubscribe link has expired. Please log in and unsubscribe manually."
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    pref_field = token_data.get("preference_field")
    if not pref_field:
        raise HTTPException(
            status_code=400,
            detail="Invalid unsubscribe link."
        )

    # Find the matching preference name
    pref_name = None
    for k, (field, name) in EMAIL_TYPE_TO_PREFERENCE.items():
        if field == pref_field:
            pref_name = name
            break

    if not pref_name:
        raise HTTPException(
            status_code=400,
            detail="Invalid unsubscribe link."
        )

    setattr(user, pref_field, False)
    await db.commit()
    return {
        "message": f"Successfully unsubscribed from {pref_name.lower()}.",
        "preference_name": pref_name
    }


@router.get("/unsubscribe")
async def unsubscribe_get(
    token: str | None = None
):
    ui_base = settings.web_ui_url.rstrip('/') if settings.web_ui_url else f"{settings.base_url.rstrip('/')}/ui"
    url = f"{ui_base}/unsubscribe"
    if token:
        url += f"?token={token}"
    return RedirectResponse(url=url)

