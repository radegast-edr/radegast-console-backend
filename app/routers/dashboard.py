from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.device_group import DeviceGroup
from app.models.log import Log, LogSeen
from app.models.team import Team
from app.models.user import User
from app.schemas.dashboard import DashboardResponse
from app.schemas.device import DeviceResponse
from app.schemas.log import LogResponse
from app.schemas.team import DeviceGroupResponse, TeamResponse
from app.services.logs import filter_logs
from app.services.permissions import get_user_team_ids_transitive

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/", response_model=DashboardResponse)
async def get_dashboard_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return DashboardResponse(
            teams=[],
            groups=[],
            devices=[],
            logs=[],
            team_device_counts={},
            group_device_counts={},
            device_groups_map={},
            device_teams_map={},
        )

    # 1. Fetch teams, groups, and devices in transitive team IDs
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.users), selectinload(Team.groups).selectinload(DeviceGroup.devices))
        .where(Team.id.in_(list(team_ids)))
    )
    teams_list = result.scalars().all()

    # 2. Extract unique groups and devices, and build counts/mappings
    groups_dict = {}
    teams_mapped = []

    for team in teams_list:
        teams_mapped.append(
            TeamResponse(
                id=team.id,
                name=team.name,
                permission_pack=team.permission_pack,
                permission_invite=team.permission_invite,
                permission_admin=team.permission_admin,
                permission_logs=team.permission_logs,
                managing_team_id=team.managing_team_id,
            )
        )
        for g in team.groups:
            if g.id not in groups_dict:
                groups_dict[g.id] = g

    groups_mapped = [DeviceGroupResponse(id=g.id, name=g.name) for g in groups_dict.values()]

    devices_dict = {}
    for g in groups_dict.values():
        for d in g.devices:
            if d.id not in devices_dict:
                devices_dict[d.id] = d

    devices_mapped = [
        DeviceResponse(
            id=d.id,
            name=d.name,
            signature_public_key=d.signature_public_key,
            encryption_public_key=d.encryption_public_key,
            last_seen=d.last_seen,
            agent_version=d.agent_version,
            rustinel_version=d.rustinel_version,
            os=d.os,
        )
        for d in devices_dict.values()
    ]

    # Team device counts
    team_device_counts = {}
    for team in teams_list:
        team_devs = set()
        for g in team.groups:
            for d in g.devices:
                team_devs.add(d.id)
        team_device_counts[team.id] = len(team_devs)

    # Group device counts
    group_device_counts = {}
    for g in groups_dict.values():
        group_device_counts[g.id] = len(g.devices)

    # Mappings
    device_groups_map = {}
    for g in groups_dict.values():
        for d in g.devices:
            if d.id not in device_groups_map:
                device_groups_map[d.id] = []
            device_groups_map[d.id].append(g.name)

    device_teams_map = {}
    for team in teams_list:
        for g in team.groups:
            for d in g.devices:
                if d.id not in device_teams_map:
                    device_teams_map[d.id] = []
                if team.name not in device_teams_map[d.id]:
                    device_teams_map[d.id].append(team.name)

    # 3. Fetch latest 1000 logs for visible devices
    visible_device_ids = list(devices_dict.keys())
    logs_mapped = []

    if visible_device_ids:
        query = select(Log).where(Log.device_id.in_(visible_device_ids))
        query = filter_logs(query, from_time=None, to_time=None, min_level=None, user=user)
        query = query.order_by(Log.time.desc()).limit(1000)
        logs_res = await db.execute(query)
        logs_list = logs_res.scalars().all()

        seen_log_ids = set()
        if logs_list:
            log_ids = [log.id for log in logs_list]
            seen_res = await db.execute(select(LogSeen.log_id).where(LogSeen.user_id == user.id, LogSeen.log_id.in_(log_ids)))
            seen_log_ids = set(seen_res.scalars().all())

        for log in logs_list:
            seen = log.id in seen_log_ids
            logs_mapped.append(
                LogResponse(
                    id=log.id,
                    device_id=log.device_id,
                    time=log.time,
                    content=log.content,
                    signature=log.signature,
                    seen=seen,
                    severity=log.severity,
                    alert_resolution=log.alert_resolution,
                    triage_note=log.triage_note,
                    rule_id=log.rule_id,
                )
            )

    return DashboardResponse(
        teams=teams_mapped,
        groups=groups_mapped,
        devices=devices_mapped,
        logs=logs_mapped,
        team_device_counts=team_device_counts,
        group_device_counts=group_device_counts,
        device_groups_map=device_groups_map,
        device_teams_map=device_teams_map,
    )
