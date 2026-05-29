from datetime import datetime, timedelta, timezone as tz

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.middleware.session import create_session_cookie
from app.models.device_group import DeviceGroup
from app.models.team import Team
from app.models.user import User
from app.models.public_key import PublicKey
from app.models.device import Device
from app.models.associations import team_device_groups, team_users
from app.schemas.user import (
    KeyRecoverRequest,
    KeyRecoverResponse,
    KeySetupResponse,
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
from app.services.crypto import (
    decrypt_aes_gcm,
    encrypt_aes_gcm,
    generate_aes_key,
    generate_age_keypair,
)
from app.services.email import send_verification_email
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    existing: User | None = (await db.execute(select(User).where(User.email == data.email))).scalar_one_or_none()
    if existing:
        if not existing.verified:
            if datetime.now(tz=tz.utc) - existing.registered_on > timedelta(hours=24):
                await send_verification_email(data.email)
                raise HTTPException(status_code=400, detail="Email not verified. A new verification email has been sent.")
            else:
                raise HTTPException(status_code=400, detail="Email not verified. Please check your inbox.")

        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=data.email,
        password=hash_password(data.password),
        password_change=datetime.utcnow(),
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
async def login(data: UserLogin, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    cookie = create_session_cookie("user", user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie,
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age,
    )

    return {"message": "Login successful", "user_id": user.id}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=settings.session_cookie_name)
    return {"message": "Logged out"}


@router.post("/keys/setup", response_model=KeySetupResponse)
async def setup_keys(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Check if user already has keys
    result = await db.execute(
        select(PublicKey).where(PublicKey.user_id == user.id)
    )
    existing = result.scalars().all()
    if existing:
        raise HTTPException(status_code=400, detail="Keys already set up")

    # Generate regular keypair
    pub_key, priv_key = generate_age_keypair()
    regular_key = PublicKey(
        public_key=pub_key,
        private_key=None,  # Client stores private key
        key_type="regular",
        user_id=user.id,
    )
    db.add(regular_key)

    # Generate recovery keypair
    rec_pub_key, rec_priv_key = generate_age_keypair()
    recovery_aes_key = generate_aes_key()
    encrypted_priv = encrypt_aes_gcm(rec_priv_key, recovery_aes_key)
    recovery_key = PublicKey(
        public_key=rec_pub_key,
        private_key=encrypted_priv,  # Encrypted with AES
        key_type="recovery",
        user_id=user.id,
    )
    db.add(recovery_key)

    await db.commit()

    return KeySetupResponse(
        public_key=pub_key,
        recovery_key=recovery_aes_key,
    )


@router.post("/keys/recover", response_model=KeyRecoverResponse)
async def recover_keys(
    data: KeyRecoverRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PublicKey).where(
            PublicKey.user_id == user.id, PublicKey.key_type == "recovery"
        )
    )
    recovery_key = result.scalar_one_or_none()
    if not recovery_key or not recovery_key.private_key:
        raise HTTPException(status_code=404, detail="No recovery key found")

    decrypted = decrypt_aes_gcm(recovery_key.private_key, data.recovery_key)
    if not decrypted:
        raise HTTPException(status_code=400, detail="Invalid recovery key")

    return KeyRecoverResponse(
        private_key=decrypted,
        public_key=recovery_key.public_key,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


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
