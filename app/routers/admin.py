from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
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
    result = await db.execute(select(User))
    return result.scalars().all()


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
    result = await db.execute(select(Pack))
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
