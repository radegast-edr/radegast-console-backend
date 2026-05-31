from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.associations import device_group_devices
from app.models.device import Device
from app.utils import utc_now
from app.models.device_group import DeviceGroup
from app.models.team import Team
from app.models.user import User
from app.schemas.device import (
    DeviceCreate,
    DeviceCreateResponse,
    DeviceDetailResponse,
    DeviceRename,
    DeviceResponse,
    DeviceSetSigningKey,
)
from app.services.auth import generate_token, hash_token
from app.services.permissions import (
    get_user_team_ids_transitive,
    is_user_member_of_team_transitive,
    has_team_admin_permission,
    has_device_admin_permission,
)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/", response_model=DeviceCreateResponse)
async def create_device(
    data: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == data.group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    has_access = False
    for team in group.teams:
        if await has_team_admin_permission(team.id, user.id, db):
            has_access = True
            break
    if not has_access:
        raise HTTPException(status_code=403, detail="No admin permission on any team for this group")

    raw_token = generate_token()
    device = Device(
        name=data.name,
        token=hash_token(raw_token),
    )
    db.add(device)
    await db.flush()
    await db.execute(
        insert(device_group_devices).values(device_group_id=group.id, device_id=device.id)
    )
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
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return []
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.groups).selectinload(DeviceGroup.devices))
        .where(Team.id.in_(list(team_ids)))
    )
    teams = result.scalars().all()
    devices = set()
    for team in teams:
        for group in team.groups:
            for device in group.devices:
                devices.add(device)

    return [
        DeviceResponse(id=d.id, name=d.name, signature_public_key=d.signature_public_key, last_seen=d.last_seen)
        for d in devices
    ]


@router.get("/{device_id}", response_model=DeviceDetailResponse)
async def get_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Verify user can see this device (member of at least one team owning any of its groups)
    visible = False
    for group in device.groups:
        for team in group.teams:
            if await is_user_member_of_team_transitive(team.id, user.id, db):
                visible = True
                break
        if visible:
            break
    if not visible:
        raise HTTPException(status_code=403, detail="Not authorized")

    return DeviceDetailResponse(
        id=device.id,
        name=device.name,
        signature_public_key=device.signature_public_key,
        last_seen=device.last_seen,
        groups=[{"id": g.id, "name": g.name} for g in device.groups],
    )


@router.delete("/{device_id}/groups/{group_id}")
async def remove_device_from_group(
    device_id: int,
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users), selectinload(DeviceGroup.devices))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    has_access = False
    for team in group.teams:
        if await has_team_admin_permission(team.id, user.id, db):
            has_access = True
            break
    if not has_access:
        raise HTTPException(status_code=403, detail="No admin permission on any team for this group")

    result = await db.execute(
        select(Device).options(selectinload(Device.groups)).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device in group.devices:
        group.devices.remove(device)
        await db.commit()

    return {"message": "Device removed from group"}


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

    has_access = False
    for team in group.teams:
        if await has_team_admin_permission(team.id, user.id, db):
            has_access = True
            break
    if not has_access:
        raise HTTPException(status_code=403, detail="No admin permission on any team for this group")

    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.groups:
        if not await has_device_admin_permission(device_id, user.id, db):
            raise HTTPException(status_code=403, detail="No admin permission on this device")

    if device not in group.devices:
        group.devices.append(device)
        await db.commit()

    return {"message": "Device added to group"}


@router.patch("/{device_id}", response_model=DeviceResponse)
async def rename_device(
    device_id: int,
    data: DeviceRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if not await has_device_admin_permission(device_id, user.id, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    device.name = data.name
    await db.commit()
    return DeviceResponse(id=device.id, name=device.name, signature_public_key=device.signature_public_key, last_seen=device.last_seen)


@router.post("/signing-key")
async def set_signing_key(
    data: DeviceSetSigningKey,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    if device.signature_public_key is not None:
        raise HTTPException(status_code=400, detail="Signing key already set")
    device.signature_public_key = data.signature_public_key
    await db.commit()
    return {"message": "Signing key set"}


@router.post("/{device_id}/reinstall", response_model=DeviceCreateResponse)
async def reinstall_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if not await has_device_admin_permission(device_id, user.id, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    raw_token = generate_token()
    device.token = hash_token(raw_token)
    device.token_change = utc_now()
    await db.commit()
    await db.refresh(device)

    return DeviceCreateResponse(
        id=device.id,
        name=device.name,
        token=raw_token,
    )


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
