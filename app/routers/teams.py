from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.associations import team_device_groups, team_users
from app.models.device_group import DeviceGroup
from app.models.team import Team
from app.models.user import User
from app.schemas.team import (
    DeviceGroupCreate,
    DeviceGroupResponse,
    TeamCreate,
    TeamInvite,
    TeamResponse,
    TeamUpdate,
)
from app.services.email import send_invite_email

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("/", response_model=list[TeamResponse])
async def list_teams(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.users))
        .where(Team.users.any(User.id == user.id))
    )
    teams = result.scalars().all()
    return teams


@router.post("/", response_model=TeamResponse)
async def create_team(
    data: TeamCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = Team(
        name=data.name,
        permission_pack=data.permission_pack,
        permission_invite=data.permission_invite,
        permission_admin=data.permission_admin,
        permission_logs=data.permission_logs,
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


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: int,
    data: TeamUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if team.permission_admin is None:
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    if data.name is not None:
        team.name = data.name
    if data.permission_pack is not None:
        team.permission_pack = data.permission_pack
    if data.permission_invite is not None:
        team.permission_invite = data.permission_invite
    if data.permission_admin is not None:
        team.permission_admin = data.permission_admin
    if data.permission_logs is not None:
        team.permission_logs = data.permission_logs

    await db.commit()
    await db.refresh(team)
    return team


@router.post("/{team_id}/invite")
async def invite_to_team(
    team_id: int,
    data: TeamInvite,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if team.permission_invite is None:
        raise HTTPException(status_code=403, detail="No invite permission on this team")

    await send_invite_email(data.email, team.id, team.name)
    return {"message": f"Invitation sent to {data.email}"}


@router.get("/{team_id}/members", response_model=list[dict])
async def list_members(
    team_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    return [{"id": u.id, "email": u.email, "role": u.role.value} for u in team.users]


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(
    team_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if team.permission_admin is None:
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
    team = await _get_user_team(team_id, user, db)
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
    if team.permission_admin is None:
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    group = DeviceGroup(name=data.name)
    db.add(group)
    await db.flush()
    team.groups.append(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.post("/{team_id}/groups/{group_id}/link")
async def link_group_to_team(
    team_id: int,
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_user_team(team_id, user, db)
    if team.permission_admin is None:
        raise HTTPException(status_code=403, detail="No admin permission on this team")

    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    if group not in team.groups:
        await db.execute(
            insert(team_device_groups).values(team_id=team.id, device_group_id=group.id)
        )
        await db.commit()

    return {"message": "Group linked to team"}


@router.get("/{team_id}/devices")
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
    if user not in team.users:
        raise HTTPException(status_code=403, detail="Not a member of this team")
    return team
