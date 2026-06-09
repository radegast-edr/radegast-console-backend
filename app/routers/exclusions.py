from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.exclusion import Exclusion
from app.models.team import Team
from app.models.user import User
from app.schemas.exclusion import ExclusionCreate, ExclusionResponse
from app.services.permissions import (
    get_user_team_ids_transitive,
    has_team_pack_permission,
    is_user_member_of_team_transitive,
)

router = APIRouter(prefix="/exclusions", tags=["exclusions"])


class ExclusionListResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime


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


@router.get("/", response_model=list[ExclusionListResponse])
async def list_all_exclusions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all exclusions visible to the current user."""
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return []

    # Get all device groups accessible to user's teams
    result = await db.execute(
        select(DeviceGroup)
        .where(DeviceGroup.teams.any(Team.id.in_(team_ids)))  # type: ignore
    )
    groups = result.scalars().all()

    if not groups:
        return []

    group_ids = [g.id for g in groups]

    # Get all exclusions for these groups
    result = await db.execute(
        select(Exclusion).where(Exclusion.device_group_id.in_(group_ids))
    )
    exclusions = result.scalars().all()

    return [
        {
            "id": e.id,
            "device_group_id": e.device_group_id,
            "name": e.name,
            "description": e.description,
            "jsonata_query": e.jsonata_query,
            "created_at": e.created_at,
        }
        for e in exclusions
    ]


@router.get("/groups/{group_id}", response_model=list[ExclusionResponse])
async def list_group_exclusions(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all exclusions for a specific device group."""
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_can_view_group(group, user, db):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(Exclusion).where(Exclusion.device_group_id == group_id)
    )
    exclusions = result.scalars().all()

    return [
        ExclusionResponse(
            id=e.id,
            device_group_id=e.device_group_id,
            name=e.name,
            description=e.description,
            jsonata_query=e.jsonata_query,
            created_at=e.created_at,
        )
        for e in exclusions
    ]


@router.post("/groups/{group_id}", response_model=ExclusionResponse)
async def create_exclusion(
    group_id: int,
    data: ExclusionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new exclusion for a device group."""
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_pack_write_on_group(group, user, db):
        raise HTTPException(status_code=403, detail="No pack write permission")

    exclusion = Exclusion(
        device_group_id=group_id,
        name=data.name,
        description=data.description,
        jsonata_query=data.jsonata_query,
    )
    db.add(exclusion)
    await db.commit()
    await db.refresh(exclusion)

    return ExclusionResponse(
        id=exclusion.id,
        device_group_id=exclusion.device_group_id,
        name=exclusion.name,
        description=exclusion.description,
        jsonata_query=exclusion.jsonata_query,
        created_at=exclusion.created_at,
    )


# Device-facing endpoint for downloading exclusions
@router.get("/device")
async def get_device_exclusions(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """
    Device endpoint: Returns all exclusions (JSONata queries) for the groups this device belongs to.
    The agent will use these to filter out false positives from alerts.
    """
    # Get all groups this device belongs to
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.devices.any(Device.id == device.id))  # type: ignore
    )
    groups = result.scalars().all()

    if not groups:
        return JSONResponse(content={"exclusions": []})

    group_ids = [g.id for g in groups]

    # Get all exclusions for these groups
    result = await db.execute(
        select(Exclusion).where(Exclusion.device_group_id.in_(group_ids))
    )
    exclusions = result.scalars().all()

    # Return as a simple array of {name, jsonata_query} for the device to use
    return JSONResponse(content={
        "exclusions": [
            {
                "id": e.id,
                "name": e.name,
                "jsonata_query": e.jsonata_query,
                "device_group_id": e.device_group_id,
            }
            for e in exclusions
        ]
    })


@router.delete("/{exclusion_id}", response_model=MessageResponse)
async def delete_exclusion(
    exclusion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an exclusion."""
    result = await db.execute(
        select(Exclusion)
        .where(Exclusion.id == exclusion_id)
    )
    exclusion = result.scalar_one_or_none()
    if not exclusion:
        raise HTTPException(status_code=404, detail="Exclusion not found")

    # Get the group to check permissions
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.id == exclusion.device_group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_pack_write_on_group(group, user, db):
        raise HTTPException(status_code=403, detail="No pack write permission")

    await db.delete(exclusion)
    await db.commit()

    return {"message": "Exclusion deleted"}


@router.get("/{exclusion_id}", response_model=ExclusionResponse)
async def get_exclusion(
    exclusion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific exclusion by ID."""
    result = await db.execute(
        select(Exclusion).where(Exclusion.id == exclusion_id)
    )
    exclusion = result.scalar_one_or_none()
    if not exclusion:
        raise HTTPException(status_code=404, detail="Exclusion not found")

    # Check if user can view the group
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.id == exclusion.device_group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_can_view_group(group, user, db):
        raise HTTPException(status_code=403, detail="Not authorized")

    return ExclusionResponse(
        id=exclusion.id,
        device_group_id=exclusion.device_group_id,
        name=exclusion.name,
        description=exclusion.description,
        jsonata_query=exclusion.jsonata_query,
        created_at=exclusion.created_at,
    )
