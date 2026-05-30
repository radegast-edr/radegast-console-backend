from datetime import datetime, timedelta, timezone as tz

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.middleware.session import create_session_cookie
from app.models.device_group import DeviceGroup
from app.models.key_transfer import KeyTransfer
from app.models.team import Team
from app.models.user import User
from app.models.public_key import PublicKey
from app.models.device import Device
from app.models.associations import team_device_groups, team_users
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
    NotificationSettings,
    PublicKeyResponse,
    PublicKeyAddRequest,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.schemas.device import DeviceLogin
from app.services.auth import (
    create_signed_token,
    generate_token,
    hash_password,
    hash_token,
    verify_password,
    verify_signed_token,
)
from app.services.email import (
    send_keys_transferred_notification,
    send_login_notification,
    send_new_keys_notification,
    send_recovery_used_notification,
    send_verification_email,
    send_notification_disabled_alert,
)
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Return the real client IP, checking X-Forwarded-For first."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=UserResponse)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    existing: User | None = (await db.execute(select(User).where(User.email == data.email))).scalar_one_or_none()
    if existing:
        if not existing.verified:
            if datetime.utcnow() - existing.password_change > timedelta(hours=24):
                await send_verification_email(data.email)
                existing.password_change = datetime.utcnow()
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
):
    result = await db.execute(select(User).where(User.email == data.email))
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
            pk_obj.last_used_at = datetime.utcnow()
            # We will commit below or now. Committing now is fine.

    cookie = create_session_cookie("user", user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
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
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        verified=user.verified,
        has_keys=key_count > 0,
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
    user.password_change = datetime.now(tz=tz.utc)
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
    await db.commit()

    if disabled_features:
        await send_notification_disabled_alert(user.email, disabled_features)

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
        expires_at=datetime.utcnow() + timedelta(minutes=10),
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
    if datetime.utcnow() > transfer.expires_at:
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
    if datetime.utcnow() > transfer.expires_at:
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
