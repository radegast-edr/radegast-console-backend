import base64
import json
from datetime import datetime, timedelta
from logging import getLogger
from urllib.parse import urlparse

import pyotp
import webauthn
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import (
    get_current_user,
    get_session,
    rate_limit_mfa_otp,
    user_has_required_mfa_setup,
)
from app.middleware.session import create_session_cookie
from app.models.hardware_token import HardwareToken
from app.models.key_transfer import KeyTransfer
from app.models.public_key import PublicKey
from app.models.user import User
from app.schemas.user import (
    AiAnalysisToolSettings,
    ApiKeysEnabledSettings,
    ChangePasswordRequest,
    ExtendedEdrSettings,
    KeyRecoverResponse,
    KeySecondarySetupRequest,
    KeySetupRequest,
    KeySetupResponse,
    KeyTransferCompleteRequest,
    KeyTransferInitiateRequest,
    KeyTransferInitiateResponse,
    KeyTransferStatusResponse,
    MfaHardwareTokenResponse,
    MfaHardwareTokenSetupResponse,
    MfaHardwareTokenVerifyRequest,
    MfaOtpSetupResponse,
    MfaOtpVerifyRequest,
    MfaSettingsResponse,
    NotificationSettings,
    PublicKeyAddRequest,
    PublicKeyResponse,
    UserResponse,
)
from app.services.auth import (
    create_signed_token,
    hash_password,
    load_signed_token_without_age,
    verify_password,
    verify_signed_token,
)
from app.services.email import (
    EMAIL_TYPE_TO_PREFERENCE,
    send_api_keys_toggled_notification,
    send_keys_transferred_notification,
    send_new_keys_notification,
    send_notification_disabled_alert,
    send_recovery_used_notification,
    send_severity_changed_email,
)
from app.utils import ensure_utc, utc_now

SECURE_COOKIE = settings.environment != "dev"

router = APIRouter(prefix="/user", tags=["user"])


def _client_ip(request: Request) -> str:
    """Return the real client IP, checking CF-Connecting-IP, X-Real-IP, and X-Forwarded-For first."""
    if "cf-connecting-ip" in request.headers:
        return request.headers["cf-connecting-ip"]
    if "x-real-ip" in request.headers:
        return request.headers["x-real-ip"]
    if "x-forwarded-for" in request.headers:
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


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


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def base64url_decode(data: str) -> bytes:
    rem = len(data) % 4
    if rem > 0:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data)


@router.post("/keys/setup", response_model=KeySetupResponse)
async def setup_keys(
    data: KeySetupRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id))
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
    result = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id, PublicKey.key_type == "regular"))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="No primary key found; use /keys/setup first")

    dup = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id, PublicKey.key_type == "secondary"))
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
        raise HTTPException(
            status_code=400,
            detail="Cannot delete keys. At least one recovery key must always exist.",
        )
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
    result = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id))
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
    dup = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id, PublicKey.public_key == data.public_key))
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
    result = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id))
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
    result = await db.execute(select(PublicKey).where(PublicKey.user_id == user.id, PublicKey.private_key.isnot(None)))
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
    key_count = (await db.execute(select(func.count()).select_from(PublicKey).where(PublicKey.user_id == user.id))).scalar()

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
        extended_edr_enabled=user.extended_edr_enabled,
        api_keys_enabled=user.api_keys_enabled,
        ai_analysis_tool=user.ai_analysis_tool,
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
    if user.notify_api_key_modification and not data.notify_api_key_modification:
        disabled_features.append("API key modification")
    if user.notify_news_updates and not data.notify_news_updates:
        disabled_features.append("Platform news and updates")

    user.notify_login = data.notify_login
    user.notify_new_keys = data.notify_new_keys
    user.notify_recovery_used = data.notify_recovery_used
    user.notify_keys_transferred = data.notify_keys_transferred
    user.notify_device_log = data.notify_device_log
    user.notify_downtime_maintenance = data.notify_downtime_maintenance
    user.notify_api_key_modification = data.notify_api_key_modification
    user.notify_news_updates = data.notify_news_updates

    severity_changed = False
    old_level = user.notification_level
    if old_level != data.notification_level:
        user.notification_level = data.notification_level
        severity_changed = True

    await db.commit()

    if disabled_features:
        await send_notification_disabled_alert(user.email, disabled_features)

    if severity_changed:
        await send_severity_changed_email(user.email, old_level, data.notification_level)

    return NotificationSettings.model_validate(user)


@router.put("/extended-edr", response_model=ExtendedEdrSettings)
async def update_extended_edr(
    data: ExtendedEdrSettings,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.extended_edr_enabled = data.extended_edr_enabled
    await db.commit()
    return ExtendedEdrSettings.model_validate(user)


@router.put("/api-keys-enabled", response_model=ApiKeysEnabledSettings)
async def update_api_keys_enabled(
    data: ApiKeysEnabledSettings,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    changed = user.api_keys_enabled != data.api_keys_enabled
    user.api_keys_enabled = data.api_keys_enabled
    await db.commit()

    if changed and user.notify_api_key_modification:
        background_tasks.add_task(send_api_keys_toggled_notification, user.email, data.api_keys_enabled)

    return ApiKeysEnabledSettings.model_validate(user)


@router.put("/ai-analysis-tool", response_model=AiAnalysisToolSettings)
async def update_ai_analysis_tool(
    data: AiAnalysisToolSettings,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.ai_analysis_tool = data.ai_analysis_tool
    await db.commit()
    return AiAnalysisToolSettings.model_validate(user)


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
    result = await db.execute(select(KeyTransfer).where(KeyTransfer.id == transfer_id))
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
    result = await db.execute(select(KeyTransfer).where(KeyTransfer.id == transfer_id))
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
    _rate_limit=Depends(rate_limit_mfa_otp),
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
        path="/",
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
        salt="hardware-token-register",
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
        raise HTTPException(status_code=400, detail=f"WebAuthn verification failed: {e!s}") from e

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
        path="/",
    )

    return {"message": "Hardware token registered successfully"}


@router.get("/mfa/settings", response_model=MfaSettingsResponse)
async def get_mfa_settings(
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HardwareToken).where(HardwareToken.user_id == user.id))
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
    result = await db.execute(select(HardwareToken).where(HardwareToken.user_id == user.id))
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
            detail=f"Cannot disable OTP. Your role requires at least '{required_level}' MFA, and you have no Hardware tokens registered.",
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
        path="/",
    )

    return {"message": "OTP disabled successfully"}


@router.delete("/mfa/hardware-token/{token_id}")
async def delete_hardware_token(
    token_id: int,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HardwareToken).where(HardwareToken.user_id == user.id))
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
            detail="Cannot delete Hardware token. Your role requires Hardware token MFA, and this is your last registered Hardware token.",
        )
    if required_level == "otp" and remaining_token_count == 0 and not has_otp:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete Hardware token. Your role requires at least OTP MFA, and you do not have OTP enabled.",
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
        path="/",
    )

    return {"message": "Hardware token deleted successfully"}


@router.post("/unsubscribe")
async def unsubscribe(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.query_params.get("token")
    if not token:
        try:
            body = await request.json()
            if isinstance(body, dict):
                token = body.get("token")
        except Exception as e:
            getLogger(__name__).debug(f"Failed to parse JSON body: {e}")

    if not token:
        try:
            form = await request.form()
            token = form.get("token")
        except Exception as e:
            getLogger(__name__).debug(f"Failed to parse form data: {e}")

    if not token:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.")

    token_data = load_signed_token_without_age(token, salt="unsubscribe")
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.")

    user_id = token_data.get("user_id")
    expires_at_str = token_data.get("expires_at")
    if user_id is None or not expires_at_str:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.")

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.") from e

    expires_at = ensure_utc(expires_at)
    if utc_now() > expires_at:
        raise HTTPException(
            status_code=400,
            detail="The unsubscribe link has expired. Please log in and unsubscribe manually.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    pref_field = token_data.get("preference_field")
    if not pref_field:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.")

    # Find the matching preference name
    pref_name = None
    for _k, (field, name) in EMAIL_TYPE_TO_PREFERENCE.items():
        if field == pref_field:
            pref_name = name
            break

    if not pref_name:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.")

    setattr(user, pref_field, False)
    await db.commit()
    return {
        "message": f"Successfully unsubscribed from {pref_name.lower()}.",
        "preference_name": pref_name,
    }


@router.get("/unsubscribe")
async def unsubscribe_get(token: str | None = None):
    ui_base = settings.web_ui_url.rstrip("/") if settings.web_ui_url else f"{settings.base_url.rstrip('/')}/ui"
    url = f"{ui_base}/unsubscribe"
    if token:
        url += f"?token={token}"
    return RedirectResponse(url=url)
