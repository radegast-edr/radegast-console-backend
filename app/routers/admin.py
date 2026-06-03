from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.device import Device
from app.models.pack import Pack
from app.models.user import User, UserRole
from app.schemas.device import DeviceResponse
from app.schemas.pack import PackResponse
from app.schemas.user import UserResponse
from app.services.packs import delete_pack_files
from app.utils import utc_now

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(user: User):
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("/users", response_model=list[UserResponse])
async def list_all_users(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(
        select(User).options(
            selectinload(User.hardware_tokens),
            selectinload(User.public_keys)
        )
    )
    users = result.scalars().all()

    from app.config import settings
    from app.dependencies import user_has_required_mfa_setup

    response_users = []
    for u in users:
        req_level = "none"
        if u.role.value == "admin":
            req_level = settings.mfa_required_level_admin
        elif u.role.value == "maintainer":
            req_level = settings.mfa_required_level_maintainer
        elif u.role.value == "user":
            req_level = settings.mfa_required_level_user

        conf_level = "none"
        if len(u.hardware_tokens) > 0:
            conf_level = "hardware_token"
        elif u.otp_enabled and u.otp_secret:
            conf_level = "otp"

        setup_missing = not user_has_required_mfa_setup(u, req_level)

        response_users.append(
            UserResponse(
                id=u.id,
                email=u.email,
                role=u.role.value if hasattr(u.role, "value") else str(u.role),
                verified=u.verified,
                has_keys=len(u.public_keys) > 0,
                mfa_required_level=req_level,
                mfa_setup_missing=setup_missing,
                mfa_configured_level=conf_level,
            )
        )
    return response_users


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    if user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(target)
    await db.commit()
    return {"message": "User deleted"}


@router.get("/devices", response_model=list[DeviceResponse])
async def list_all_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Device))
    return result.scalars().all()


@router.delete("/devices/{device_id}")
async def admin_delete_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await db.delete(device)
    await db.commit()
    return {"message": "Device deleted"}


@router.get("/packs", response_model=list[PackResponse])
async def list_all_packs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Pack).options(selectinload(Pack.teams)))
    return result.scalars().all()


@router.delete("/packs/{pack_id}")
async def admin_delete_pack(
    pack_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Pack).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    delete_pack_files(pack_id)
    await db.delete(pack)
    await db.commit()
    return {"message": "Pack deleted"}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    result = await db.execute(
        select(User).options(selectinload(User.hardware_tokens)).where(User.id == user_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    new_password = "".join(secrets.choice(alphabet) for _ in range(12))

    from app.services.auth import hash_password
    target.password = hash_password(new_password)
    target.password_change = utc_now()

    target.otp_enabled = False
    target.otp_secret = None
    target.hardware_tokens = []

    await db.commit()

    from app.services.email import send_password_reset_email
    background_tasks.add_task(send_password_reset_email, target.email, new_password)

    return {"message": "User password reset successfully and MFA cleared"}

