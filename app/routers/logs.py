from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.associations import device_group_devices, team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.log import Log, LogSeen, LogSeverity
from app.models.pack_version_rule import PackVersionRule
from app.models.team import Team
from app.models.user import User
from app.schemas.log import (
    LogCountResponse,
    LogCreate,
    LogResolveRequest,
    LogResponse,
    TriggeredRuleResponse,
)
from app.services.email import send_device_log_notification
from app.services.logs import (
    filter_logs,
    get_visible_device_ids,
    is_sufficient_severity,
)
from app.services.permissions import get_device_encryption_keys_list
from app.services.rule_lookup import find_and_cache_triggered_rule
from app.utils import ensure_utc

router = APIRouter(prefix="/logs", tags=["logs"])


def _make_triggered_rule(pack_version_rule: PackVersionRule | None) -> TriggeredRuleResponse | None:
    if pack_version_rule is None:
        return None
    return TriggeredRuleResponse(
        rule_id=pack_version_rule.rule_id,
        rule_type=pack_version_rule.rule_type,
        pack_version_id=pack_version_rule.pack_version_id,
        rule_content=pack_version_rule.rule_content,
    )


def _make_log_response(log: Log, seen: bool, pack_version_rule: PackVersionRule | None = None) -> LogResponse:
    return LogResponse(
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
        rule_type=log.rule_type,
        triggered_rule=_make_triggered_rule(pack_version_rule),
    )


@router.post("/", response_model=LogResponse)
async def submit_log(
    data: LogCreate,
    background_tasks: BackgroundTasks,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    log = Log(
        device_id=device.id,
        time=ensure_utc(data.time),
        content=data.content,
        signature=data.signature,
        severity=data.severity,
        rule_id=data.rule_id,
        rule_type=data.rule_type,
    )
    db.add(log)
    await db.flush()  # get log.id before resolving the rule

    # If rule_id and rule_type are provided, look up the rule in enabled packs and cache it
    pack_version_rule: PackVersionRule | None = None
    if data.rule_id and data.rule_type:
        pack_version_rule = await find_and_cache_triggered_rule(device, data.rule_id, data.rule_type, db)
        if pack_version_rule is not None:
            log.pack_version_rule_id = pack_version_rule.id

    await db.commit()
    await db.refresh(log)

    # Fetch all users with log-read permission on this device
    result = await db.execute(
        select(User)
        .join(team_users, User.id == team_users.c.user_id)
        .join(Team, Team.id == team_users.c.team_id)
        .join(team_device_groups, team_device_groups.c.team_id == Team.id)
        .join(
            device_group_devices,
            device_group_devices.c.device_group_id == team_device_groups.c.device_group_id,
        )
        .where(
            device_group_devices.c.device_id == device.id,
            Team.permission_logs == "read",
        )
        .distinct()
    )
    users = result.scalars().all()

    for u in users:
        if log.severity and not is_sufficient_severity(log.severity, u.notification_level):
            db.add(LogSeen(user_id=u.id, log_id=log.id))
        else:
            if u.notify_device_log:
                background_tasks.add_task(
                    send_device_log_notification,
                    u.email,
                    device.name,
                    device.id,
                    log.severity,
                    log.id,
                    log.time,
                )

    await db.commit()

    return _make_log_response(log, seen=False, pack_version_rule=pack_version_rule)


@router.post("/seen/all")
async def mark_all_logs_seen(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Get all teams user is in with log read permission
    result = await db.execute(
        select(Team)
        .options(selectinload(Team.groups).selectinload(DeviceGroup.devices))
        .where(Team.users.any(User.id == user.id), Team.permission_logs == "read")
    )
    teams = result.scalars().all()

    visible_device_ids = set()
    for team in teams:
        for group in team.groups:
            for device in group.devices:
                visible_device_ids.add(device.id)

    if not visible_device_ids:
        return {"message": "No logs to mark as seen"}

    # Fetch all log IDs visible to this user
    # In extended EDR mode, only mark logs without a resolution to avoid overwriting
    query = select(Log.id).where(Log.device_id.in_(visible_device_ids))
    if user.extended_edr_enabled:
        query = query.where(Log.alert_resolution is None)
    logs_res = await db.execute(query)
    visible_log_ids = logs_res.scalars().all()

    if visible_log_ids:
        # Find which ones are already marked seen
        existing_res = await db.execute(select(LogSeen.log_id).where(LogSeen.user_id == user.id, LogSeen.log_id.in_(visible_log_ids)))
        existing_seen_ids = set(existing_res.scalars().all())

        to_add_ids = set(visible_log_ids) - existing_seen_ids
        for log_id in to_add_ids:
            db.add(LogSeen(user_id=user.id, log_id=log_id))

        await db.commit()

    return {"message": "All logs marked as seen"}


@router.post("/{log_id}/seen")
async def mark_log_seen(
    log_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check if log exists
    log_result = await db.execute(select(Log).where(Log.id == log_id))
    log = log_result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")

    seen_result = await db.execute(select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id))
    if not seen_result.scalar_one_or_none():
        log_seen = LogSeen(user_id=user.id, log_id=log_id)
        db.add(log_seen)
        await db.commit()

    return {"message": "Log marked as seen"}


@router.get("/count", response_model=LogCountResponse)
async def get_logs_count(
    device_id: int | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    min_level: LogSeverity | None = None,
    unread_only: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    visible_device_ids = await get_visible_device_ids(user, db)

    if not visible_device_ids:
        return LogCountResponse(total_count=0)

    if device_id:
        if device_id not in visible_device_ids:
            raise HTTPException(status_code=403, detail="No log permission for this device")
        query = select(func.count(Log.id)).where(Log.device_id == device_id)
    else:
        query = select(func.count(Log.id)).where(Log.device_id.in_(visible_device_ids))

    query = filter_logs(query, from_time, to_time, min_level, user)

    if unread_only:
        if user.extended_edr_enabled:
            # Extended EDR: a log is "active" until it has an explicit resolution.
            # Seen status alone does not close an alert.
            query = query.where(or_(Log.alert_resolution.is_(None), Log.alert_resolution == "none"))
        else:
            # Basic mode: a log is "active" until the user has seen it.
            # Resolution is not required in basic mode — seeing the alert is enough.
            seen_subq = select(LogSeen.log_id).where(LogSeen.user_id == user.id, LogSeen.log_id == Log.id).exists()
            query = query.where(~seen_subq)

    count_res = await db.execute(query)
    count = count_res.scalar_one()
    return LogCountResponse(total_count=count)


@router.get("/", response_model=list[LogResponse])
async def list_logs(
    device_id: int | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    min_level: LogSeverity | None = None,
    page: int = 1,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    visible_device_ids = await get_visible_device_ids(user, db)

    if not visible_device_ids:
        return []

    offset = (page - 1) * limit

    if device_id:
        if device_id not in visible_device_ids:
            raise HTTPException(status_code=403, detail="No log permission for this device")
        query = select(Log).where(Log.device_id == device_id)
    else:
        query = select(Log).where(Log.device_id.in_(visible_device_ids))

    query = filter_logs(query, from_time, to_time, min_level, user)
    query = query.options(selectinload(Log.pack_version_rule)).order_by(Log.time.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    seen_log_ids = set()
    if logs:
        log_ids = [log.id for log in logs]
        seen_res = await db.execute(select(LogSeen.log_id).where(LogSeen.user_id == user.id, LogSeen.log_id.in_(log_ids)))
        seen_log_ids = set(seen_res.scalars().all())

    return [_make_log_response(log, seen=log.id in seen_log_ids, pack_version_rule=log.pack_version_rule) for log in logs]


@router.patch("/{log_id}/resolve", response_model=LogResponse)
async def resolve_log(
    log_id: int,
    data: LogResolveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Log).options(selectinload(Log.pack_version_rule)).where(Log.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")

    log.alert_resolution = data.alert_resolution
    log.triage_note = data.triage_note

    # Auto-mark as seen only when an actual resolution is being set.
    # In extended EDR mode, clearing the resolution (setting it to None/none)
    # should NOT mark the log as seen so it remains visually "active" until triaged.
    has_real_resolution = data.alert_resolution and data.alert_resolution != "none"
    if not user.extended_edr_enabled or has_real_resolution:
        seen_result = await db.execute(select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id))
        if not seen_result.scalar_one_or_none():
            log_seen = LogSeen(user_id=user.id, log_id=log_id)
            db.add(log_seen)

    await db.commit()
    await db.refresh(log)

    # Determine seen status for response
    seen_result = await db.execute(select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id))
    seen = seen_result.scalar_one_or_none() is not None

    return _make_log_response(log, seen=seen, pack_version_rule=log.pack_version_rule)


@router.get("/encryption-keys")
async def get_encryption_keys(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """Returns all public keys of users with log read permission for this device's groups."""
    return await get_device_encryption_keys_list(device.id, db)


@router.get("/{log_id}/encryption-keys")
async def get_log_encryption_keys_for_user(
    log_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all public keys for users with access to encrypt/decrypt this log's device."""
    # 1. Fetch log
    result = await db.execute(select(Log).where(Log.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")

    # 2. Get user's visible devices to verify permission
    visible_device_ids = await get_visible_device_ids(user, db)

    if log.device_id not in visible_device_ids:
        raise HTTPException(status_code=403, detail="No log permission for this device")

    # 3. Call shared utility
    return await get_device_encryption_keys_list(log.device_id, db)


@router.get("/{log_id}/device-keys")
async def get_log_device_keys_for_triage(
    log_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    User-accessible endpoint: returns all public keys of users with log-read access
    on the device associated with this log.  Used by the frontend to encrypt triage notes
    so that every analyst who can see the log can also decrypt the note.
    Uses the same shared utility as the device-facing encryption-keys endpoint.
    """
    # 1. Fetch log
    result = await db.execute(select(Log).where(Log.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")

    # 2. Verify the requesting user has log-read access to this device
    teams_res = await db.execute(
        select(Team)
        .options(selectinload(Team.groups).selectinload(DeviceGroup.devices))
        .where(Team.users.any(User.id == user.id), Team.permission_logs == "read")
    )
    teams = teams_res.scalars().all()

    visible_device_ids = set()
    for team in teams:
        for group in team.groups:
            for device in group.devices:
                visible_device_ids.add(device.id)

    if log.device_id not in visible_device_ids:
        raise HTTPException(status_code=403, detail="No log permission for this device")

    # 3. Return all public keys of all users with log-read access for this device's groups
    return await get_device_encryption_keys_list(log.device_id, db)


@router.get("/{log_id}", response_model=LogResponse)
async def get_log(
    log_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    visible_device_ids = await get_visible_device_ids(user, db)
    if not visible_device_ids:
        raise HTTPException(status_code=403, detail="No log permission")

    result = await db.execute(select(Log).options(selectinload(Log.pack_version_rule)).where(Log.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")

    if log.device_id not in visible_device_ids:
        raise HTTPException(status_code=403, detail="No log permission for this device")

    seen_result = await db.execute(select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id))
    seen = seen_result.scalar_one_or_none() is not None

    return _make_log_response(log, seen=seen, pack_version_rule=log.pack_version_rule)
