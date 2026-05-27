from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.team import Team
from app.models.user import User
from app.schemas.device import (
    DeviceCreate,
    DeviceCreateResponse,
    DeviceResponse,
    DeviceSetSigningKey,
)
from app.services.auth import generate_token, hash_token

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/", response_model=DeviceCreateResponse)
async def create_device(
    data: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw_token = generate_token()
    device = Device(
        name=data.name,
        token=hash_token(raw_token),
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)

    return DeviceCreateResponse(
        id=device.id,
        name=device.name,
        token=raw_token,
    )


@router.get("/", response_model=list[DeviceResponse])
async def list_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Return devices visible to user through their teams' groups
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.groups).selectinload(DeviceGroup.devices))
        .where(Team.users.any(User.id == user.id))
    )
    teams = result.scalars().all()
    devices = set()
    for team in teams:
        for group in team.groups:
            for device in group.devices:
                devices.add(device)

    return [
        DeviceResponse(id=d.id, name=d.name, signature_public_key=d.signature_public_key)
        for d in devices
    ]


@router.post("/{device_id}/groups/{group_id}")
async def add_device_to_group(
    device_id: int,
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify user has admin access to a team owning this group
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users), selectinload(DeviceGroup.devices))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    has_access = any(
        user in team.users and team.permission_admin is not None
        for team in group.teams
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="No admin permission on any team for this group")

    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device not in group.devices:
        group.devices.append(device)
        await db.commit()

    return {"message": "Device added to group"}


@router.post("/signing-key")
async def set_signing_key(
    data: DeviceSetSigningKey,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    device.signature_public_key = data.signature_public_key
    await db.commit()
    return {"message": "Signing key set"}


@router.delete("/{device_id}")
async def delete_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await db.delete(device)
    await db.commit()
    return {"message": "Device deleted"}
