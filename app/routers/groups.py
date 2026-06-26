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
from app.models.team_invitation import TeamInvitation
from app.models.user import User
from app.schemas.device import DeviceResponse
from app.schemas.exclusion import ExclusionResponse
from app.schemas.team import (
    DeviceAddPayload,
    DeviceGroupResponse,
    DeviceRemovePayload,
    GroupKeysSetup,
    GroupUnlinkPayload,
    TeamResponse,
)
from app.services.permissions import (
    get_group_recipient_public_keys,
    get_user_team_ids_transitive,
    has_group_pack_write_permission,
    has_team_admin_permission,
    is_user_member_of_team_transitive,
)

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupRename(BaseModel):
    name: str


class MessageResponse(BaseModel):
    message: str


class DeviceGroupDetail(BaseModel):
    id: int
    name: str
    teams: list[TeamResponse]
    devices: list[DeviceResponse]
    exclusions: list[ExclusionResponse]
    public_key: str | None = None
    private_key: str | None = None
    invitations: list[dict] = []
    private_key_needs_refresh: bool = False
    user_has_pack_write: bool = False


async def _user_has_admin(group: DeviceGroup, user: User, db: AsyncSession) -> bool:
    from app.services.permissions import has_team_admin_permission

    for team in group.teams:
        if await has_team_admin_permission(team.id, user.id, db):
            return True
    return False


def _group_detail(group: DeviceGroup, invitations: list[TeamInvitation] | None = None) -> dict:
    invs = invitations or []
    return {
        "id": group.id,
        "name": group.name,
        "public_key": group.public_key,
        "private_key": group.private_key,
        "private_key_needs_refresh": group.private_key_needs_refresh,
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
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "signature_public_key": d.signature_public_key,
                "encryption_public_key": d.encryption_public_key,
                "last_seen": d.last_seen,
                "agent_version": d.agent_version,
            }
            for d in group.devices
        ],
        "exclusions": [
            {
                "id": e.id,
                "device_group_id": e.device_group_id,
                "name": e.name,
                "description": e.description,
                "jsonata_query": e.jsonata_query,
                "created_at": e.created_at,
                "alert_id": e.alert_id,
                "exclusion_type": e.exclusion_type,
                "encrypted": e.encrypted,
            }
            for e in group.exclusions
        ],
        "invitations": [
            {
                "id": inv.id,
                "team_id": inv.team_id,
                "user_id": inv.user_id,
                "email": inv.email,
                "created_at": inv.created_at,
            }
            for inv in invs
        ],
    }


@router.get("/", response_model=list[DeviceGroupResponse])
async def list_groups(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all device groups visible to the current user (via their teams)."""
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return []
    result = await db.execute(select(Team).options(selectinload(Team.groups)).where(Team.id.in_(list(team_ids))))
    teams = result.scalars().all()
    seen = {}
    for team in teams:
        for group in team.groups:
            if group.id not in seen:
                has_write = await has_group_pack_write_permission(group.id, user.id, db)
                resp = DeviceGroupResponse.model_validate(group)
                resp.user_has_pack_write = has_write
                seen[group.id] = resp
    return list(seen.values())


@router.get("/needs-refresh", response_model=list[DeviceGroupResponse])
async def list_groups_needing_refresh(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all device groups that need a private key refresh and are visible to the current user."""
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return []
    result = await db.execute(
        select(DeviceGroup).join(DeviceGroup.teams).where(Team.id.in_(list(team_ids)), DeviceGroup.private_key_needs_refresh)
    )
    groups = result.scalars().all()
    seen = {}
    for group in groups:
        if group.id not in seen:
            has_write = await has_group_pack_write_permission(group.id, user.id, db)
            resp = DeviceGroupResponse.model_validate(group)
            resp.user_has_pack_write = has_write
            seen[group.id] = resp
    return list(seen.values())


@router.get("/{group_id}", response_model=DeviceGroupDetail)
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
            selectinload(DeviceGroup.exclusions),
        )
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    visible = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db):
            visible = True
            break
    if not visible:
        raise HTTPException(status_code=403, detail="Not authorized")

    team_ids = [t.id for t in group.teams]
    invitations = []
    if team_ids:
        inv_result = await db.execute(select(TeamInvitation).where(TeamInvitation.team_id.in_(team_ids)))
        invitations = inv_result.scalars().all()

    has_pack_write = await has_group_pack_write_permission(group.id, user.id, db)
    detail_dict = _group_detail(group, invitations)
    detail_dict["user_has_pack_write"] = has_pack_write
    return detail_dict


@router.patch("/{group_id}", response_model=DeviceGroupResponse)
async def rename_group(
    group_id: int,
    data: GroupRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_admin(group, user, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    group.name = data.name
    await db.commit()
    has_write = await has_group_pack_write_permission(group.id, user.id, db)
    resp = DeviceGroupResponse.model_validate(group)
    resp.user_has_pack_write = has_write
    return resp


@router.post("/{group_id}/teams/{team_id}/unlink", response_model=MessageResponse)
async def unlink_group_from_team(
    group_id: int,
    team_id: int,
    data: GroupUnlinkPayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_admin(group, user, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    if len(group.teams) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last team from a group")

    await db.execute(
        delete(team_device_groups).where(
            team_device_groups.c.team_id == team_id,
            team_device_groups.c.device_group_id == group_id,
        )
    )
    group.private_key = data.encrypted_private_key
    group.private_key_needs_refresh = True
    await db.commit()
    return {"message": "Group unlinked from team"}


@router.post("/{group_id}/devices/{device_id}", response_model=MessageResponse)
async def add_device_to_group(
    group_id: int,
    device_id: int,
    data: DeviceAddPayload,
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

    if not await _user_has_admin(group, user, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.groups:
        device_admin = False
        for g in device.groups:
            for team in g.teams:
                if await has_team_admin_permission(team.id, user.id, db):
                    device_admin = True
                    break
            if device_admin:
                break
        if not device_admin:
            raise HTTPException(status_code=403, detail="No admin permission on this device")

    if device not in group.devices:
        group.devices.append(device)
        group.private_key = data.encrypted_private_key
        group.private_key_needs_refresh = True
        await db.commit()

    return {"message": "Device added to group"}


@router.post("/{group_id}/devices/{device_id}/remove", response_model=MessageResponse)
async def remove_device_from_group(
    group_id: int,
    device_id: int,
    data: DeviceRemovePayload,
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

    if not await _user_has_admin(group, user, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    result = await db.execute(select(Device).options(selectinload(Device.groups)).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device in group.devices:
        group.devices.remove(device)
        group.private_key = data.encrypted_private_key
        group.private_key_needs_refresh = True
        await db.commit()

    return {"message": "Device removed from group"}


@router.get("/{group_id}/recipient-public-keys", response_model=list[str])
async def get_recipient_public_keys(
    group_id: int,
    exclude_user_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    visible = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db):
            visible = True
            break
    if not visible:
        raise HTTPException(status_code=403, detail="Not authorized")

    return await get_group_recipient_public_keys(group_id, db, exclude_user_id=exclude_user_id)


@router.post("/{group_id}/keys", response_model=MessageResponse)
async def setup_group_keys(
    group_id: int,
    data: GroupKeysSetup,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_admin(group, user, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    group.public_key = data.public_key
    group.private_key = data.private_key
    group.private_key_needs_refresh = False
    await db.commit()
    return {"message": "Group keys set up successfully"}


@router.delete("/{group_id}", response_model=MessageResponse)
async def delete_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not await _user_has_admin(group, user, db):
        raise HTTPException(status_code=403, detail="No admin permission")

    await db.delete(group)
    await db.commit()
    return {"message": "Group deleted successfully"}
