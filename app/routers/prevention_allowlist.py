from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.prevention_allowlist import PreventionAllowlist
from app.models.user import User
from app.schemas.prevention_allowlist import PreventionAllowlistCreate, PreventionAllowlistResponse
from app.services.permissions import (
    has_team_pack_permission,
    is_user_member_of_team_transitive,
)

router = APIRouter(prefix="/prevention-allowlist", tags=["prevention-allowlist"])


class MessageResponse(BaseModel):
    message: str


async def _user_has_pack_write_on_group(group: DeviceGroup, user: User, db: AsyncSession) -> bool:
    """Check if user has pack write permission on any team that owns this group."""
    for team in group.teams:
        if await has_team_pack_permission(team.id, user.id, db):
            return True
    return False


async def _user_can_view_group(group: DeviceGroup, user: User, db: AsyncSession) -> bool:
    """Check if user can view this group (member of any team that owns it)."""
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db):
            return True
    return False


@router.get("/groups/{group_id}", response_model=list[PreventionAllowlistResponse])
async def list_group_entries(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all prevention allowlist entries for a specific device group."""
    result = await db.execute(select(DeviceGroup).options(selectinload(DeviceGroup.teams)).where(DeviceGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_can_view_group(group, user, db):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(PreventionAllowlist).where(PreventionAllowlist.device_group_id == group_id))
    entries = result.scalars().all()

    return entries


@router.post("/groups/{group_id}", response_model=PreventionAllowlistResponse)
async def create_entry(
    group_id: int,
    data: PreventionAllowlistCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new prevention allowlist entry for a device group."""
    result = await db.execute(select(DeviceGroup).options(selectinload(DeviceGroup.teams)).where(DeviceGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_pack_write_on_group(group, user, db):
        raise HTTPException(status_code=403, detail="No pack write permission")

    entry = PreventionAllowlist(
        device_group_id=group_id,
        entry_type=data.entry_type,
        value=data.value,
        description=data.description,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    return entry


@router.get("/device")
async def get_device_entries(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """
    Device endpoint: Returns all prevention allowlist entries for the groups this device belongs to.
    """
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams)).where(DeviceGroup.devices.any(Device.id == device.id))  # type: ignore
    )
    groups = result.scalars().all()

    if not groups:
        return JSONResponse(content={"entries": [], "group_keys": {}})

    group_ids = [g.id for g in groups]

    result = await db.execute(select(PreventionAllowlist).where(PreventionAllowlist.device_group_id.in_(group_ids)))
    entries = result.scalars().all()

    group_keys = {
        g.id: {
            "public_key": g.public_key,
            "private_key": g.private_key,
        }
        for g in groups
    }

    return JSONResponse(
        content={
            "entries": [
                {
                    "id": e.id,
                    "device_group_id": e.device_group_id,
                    "entry_type": e.entry_type,
                    "value": e.value,
                    "description": e.description,
                }
                for e in entries
            ],
            "group_keys": group_keys,
        }
    )


@router.delete("/{entry_id}", response_model=MessageResponse)
async def delete_entry(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a prevention allowlist entry."""
    result = await db.execute(select(PreventionAllowlist).where(PreventionAllowlist.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    result = await db.execute(select(DeviceGroup).options(selectinload(DeviceGroup.teams)).where(DeviceGroup.id == entry.device_group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_pack_write_on_group(group, user, db):
        raise HTTPException(status_code=403, detail="No pack write permission")

    await db.delete(entry)
    await db.commit()

    return {"message": "Entry deleted"}
