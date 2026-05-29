from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.associations import team_device_groups
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.team import Team
from app.models.user import User
from app.schemas.team import DeviceGroupResponse

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupRename(BaseModel):
    name: str


def _user_has_admin(group: DeviceGroup, user: User) -> bool:
    return any(user in team.users and team.permission_admin is not None for team in group.teams)


def _group_detail(group: DeviceGroup) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "teams": [
            {
                "id": t.id,
                "name": t.name,
                "permission_pack": t.permission_pack,
                "permission_invite": t.permission_invite,
                "permission_admin": t.permission_admin,
                "permission_logs": t.permission_logs,
            }
            for t in group.teams
        ],
        "devices": [{"id": d.id, "name": d.name, "signature_public_key": d.signature_public_key} for d in group.devices],
    }


@router.get("/")
async def list_groups(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all device groups visible to the current user (via their teams)."""
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.groups))
        .where(Team.users.any(User.id == user.id))
    )
    teams = result.scalars().all()
    seen = {}
    for team in teams:
        for group in team.groups:
            if group.id not in seen:
                seen[group.id] = {"id": group.id, "name": group.name}
    return list(seen.values())


@router.get("/{group_id}")
async def get_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup)
        .options(
            selectinload(DeviceGroup.teams).selectinload(Team.users),
            selectinload(DeviceGroup.devices),
        )
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    visible = any(user in team.users for team in group.teams)
    if not visible:
        raise HTTPException(status_code=403, detail="Not authorized")

    return _group_detail(group)


@router.patch("/{group_id}")
async def rename_group(
    group_id: int,
    data: GroupRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not _user_has_admin(group, user):
        raise HTTPException(status_code=403, detail="No admin permission")

    group.name = data.name
    await db.commit()
    return {"id": group.id, "name": group.name}


@router.delete("/{group_id}/teams/{team_id}")
async def unlink_group_from_team(
    group_id: int,
    team_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not _user_has_admin(group, user):
        raise HTTPException(status_code=403, detail="No admin permission")

    if len(group.teams) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last team from a group")

    await db.execute(
        delete(team_device_groups).where(
            team_device_groups.c.team_id == team_id,
            team_device_groups.c.device_group_id == group_id,
        )
    )
    await db.commit()
    return {"message": "Group unlinked from team"}


@router.post("/{group_id}/devices/{device_id}")
async def add_device_to_group(
    group_id: int,
    device_id: int,
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

    if not _user_has_admin(group, user):
        raise HTTPException(status_code=403, detail="No admin permission")

    result = await db.execute(
        select(Device).options(selectinload(Device.groups)).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device not in group.devices:
        group.devices.append(device)
        await db.commit()

    return {"message": "Device added to group"}


@router.delete("/{group_id}/devices/{device_id}")
async def remove_device_from_group(
    group_id: int,
    device_id: int,
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

    if not _user_has_admin(group, user):
        raise HTTPException(status_code=403, detail="No admin permission")

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
