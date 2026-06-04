from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.associations import team_device_groups, team_users
from app.models.device_group import DeviceGroup
from app.models.team import Team
from app.models.user import User
from app.models.pack import Pack
from pydantic import BaseModel
from app.schemas.team import (
    DeviceGroupCreate,
    DeviceGroupResponse,
    TeamCreate,
    TeamInvite,
    TeamResponse,
    TeamUpdate,
    TeamMemberResponse,
)
from app.schemas.user import UserResponse
from app.schemas.device import DeviceResponse

class MessageResponse(BaseModel):
    message: str
from app.services.email import send_invite_email

from app.services.permissions import (
    get_user_team_ids_transitive,
    is_user_member_of_team_transitive,
    has_team_admin_permission,
)

router = APIRouter(prefix="/teams", tags=["teams"])


async def verify_admin_chain(
    team_id: int | None,
    permission_admin: str | None,
    managing_team_id: int | None,
    db: AsyncSession,
) -> None:
    """
    Verify that the team will either have admin=write or lead to a team with admin=write somewhere in the chain.
    """
    if permission_admin == "write":
        return

    if managing_team_id is None:
        raise HTTPException(
            status_code=400,
            detail="Team must have admin='write' or specify a managing team to prevent permanent lockout.",
        )

    # Walk up the chain of managing teams to verify that at least one has admin=write
    curr_id = managing_team_id
    visited = set()
    if team_id is not None:
        visited.add(team_id)

    has_write = False
    while curr_id is not None and curr_id not in visited:
        visited.add(curr_id)
        res = await db.execute(select(Team).where(Team.id == curr_id))
        mt = res.scalar_one_or_none()
        if not mt:
            break
        if mt.permission_admin == "write":
            has_write = True
            break
        curr_id = mt.managing_team_id

    if not has_write:
        raise HTTPException(
            status_code=400,
            detail="Every team must have admin='write' or have a team with admin='write' somewhere in its managing chain.",
        )


@router.get("/", response_model=list[TeamResponse])
async def list_teams(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return []
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.users))
        .where(Team.id.in_(list(team_ids)))
    )
    teams = result.scalars().all()
    return teams


@router.post("/", response_model=TeamResponse)
async def create_team(
    data: TeamCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.managing_team_id is not None:
        if not await has_team_admin_permission(data.managing_team_id, user.id, db):
            raise HTTPException(
                status_code=403,
                detail="No admin permission on the managing team",
            )

    await verify_admin_chain(None, data.permission_admin, data.managing_team_id, db)

    team = Team(
        name=data.name,
        permission_pack=data.permission_pack,
        permission_invite=data.permission_invite,
        permission_admin=data.permission_admin,
        permission_logs=data.permission_logs,
        managing_team_id=data.managing_team_id,
    )
    db.add(team)
    await db.flush()
    await db.execute(insert(team_users).values(team_id=team.id, user_id=user.id))
    await db.commit()
    await db.refresh(team)
    return team


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    return team


async def verify_team_pack_constraint(
    team_id: int,
    new_permission_pack: str | None,
    db: AsyncSession,
) -> None:
    if new_permission_pack == "write":
        return

    res = await db.execute(
        select(Team)
        .options(selectinload(Team.packs).selectinload(Pack.teams))
        .where(Team.id == team_id)
    )
    team = res.scalar_one_or_none()
    if not team:
        return

    for pack in team.packs:
        other_write_teams = [t for t in pack.teams if t.id != team_id and t.permission_pack == "write"]
        if not other_write_teams:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot change pack permission. Private pack '{pack.name}' must belong to at least one team with write permission.",
            )


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: int,
    data: TeamUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if not await has_team_admin_permission(team_id, user.id, db):
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    # If updating managing_team_id, verify user has admin access to that managing team
    if "managing_team_id" in data.model_fields_set and data.managing_team_id is not None:
        if not await has_team_admin_permission(data.managing_team_id, user.id, db):
            raise HTTPException(
                status_code=403,
                detail="No admin permission on the managing team",
            )

    # Validate the resulting admin chain
    proposed_admin = (
        data.permission_admin
        if "permission_admin" in data.model_fields_set
        else team.permission_admin
    )
    proposed_managing = (
        data.managing_team_id
        if "managing_team_id" in data.model_fields_set
        else team.managing_team_id
    )
    await verify_admin_chain(team.id, proposed_admin, proposed_managing, db)

    if "name" in data.model_fields_set:
        team.name = data.name
    if "permission_pack" in data.model_fields_set:
        if team.permission_pack == "write" and data.permission_pack != "write":
            await verify_team_pack_constraint(team.id, data.permission_pack, db)
        team.permission_pack = data.permission_pack
    if "permission_invite" in data.model_fields_set:
        team.permission_invite = data.permission_invite
    if "permission_admin" in data.model_fields_set:
        team.permission_admin = data.permission_admin
    if "permission_logs" in data.model_fields_set:
        team.permission_logs = data.permission_logs
    if "managing_team_id" in data.model_fields_set:
        team.managing_team_id = data.managing_team_id

    await db.commit()
    await db.refresh(team)
    return team


@router.post("/{team_id}/invite", response_model=MessageResponse)
async def invite_to_team(
    team_id: int,
    data: TeamInvite,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    is_admin = await has_team_admin_permission(team_id, user.id, db)
    if not is_admin and team.permission_invite is None:
        raise HTTPException(status_code=403, detail="No invite permission on this team")

    await send_invite_email(data.email, team.id, team.name)
    return {"message": f"Invitation sent to {data.email}"}


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_members(
    team_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    return [{"id": u.id, "email": u.email, "role": u.role.value} for u in team.users]


@router.delete("/{team_id}/members/{user_id}", response_model=MessageResponse)
async def remove_member(
    team_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if not await has_team_admin_permission(team_id, user.id, db):
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    # For teams with admin=write, ensure at least one admin always remains
    if len(team.users) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove last admin from team")

    target = next((u for u in team.users if u.id == user_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="User not in team")

    team.users.remove(target)
    await db.commit()
    return {"message": "Member removed"}


# Device groups within teams
@router.get("/{team_id}/groups", response_model=list[DeviceGroupResponse])
async def list_team_groups(
    team_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_team(team_id, user, db)
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.teams.any(Team.id == team_id))
    )
    groups = result.scalars().all()
    return groups


@router.post("/{team_id}/groups", response_model=DeviceGroupResponse)
async def create_team_group(
    team_id: int,
    data: DeviceGroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if not await has_team_admin_permission(team_id, user.id, db):
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    group = DeviceGroup(name=data.name)
    db.add(group)
    await db.flush()
    team.groups.append(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.post("/{team_id}/groups/{group_id}/link", response_model=MessageResponse)
async def link_group_to_team(
    team_id: int,
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if not await has_team_admin_permission(team_id, user.id, db):
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    # Verify user has admin access to this group (i.e. has admin permission on at least one team this group currently belongs to)
    group_admin = False
    for t in group.teams:
        if await has_team_admin_permission(t.id, user.id, db):
            group_admin = True
            break
    if not group_admin:
        raise HTTPException(status_code=403, detail="No admin permission on this device group")

    if group not in team.groups:
        await db.execute(
            insert(team_device_groups).values(team_id=team.id, device_group_id=group.id)
        )
        await db.commit()

    return {"message": "Group linked to team"}


@router.get("/{team_id}/devices", response_model=list[DeviceResponse])
async def list_team_devices(
    team_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.device import Device
    from app.schemas.device import DeviceResponse

    team = await _get_user_team(team_id, user, db)
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.devices))
        .where(DeviceGroup.id.in_([g.id for g in team.groups]))
    )
    groups = result.scalars().all()
    seen: dict[int, Device] = {}
    for g in groups:
        for d in g.devices:
            seen[d.id] = d
    return [DeviceResponse(id=d.id, name=d.name, signature_public_key=d.signature_public_key) for d in seen.values()]


async def _get_user_team(team_id: int, user: User, db: AsyncSession) -> Team:
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.users), selectinload(Team.groups))
        .where(Team.id == team_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not await is_user_member_of_team_transitive(team_id, user.id, db):
        raise HTTPException(status_code=403, detail="Not a member of this team")
    return team
