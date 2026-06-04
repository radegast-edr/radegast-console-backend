from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.utils import ensure_utc
from app.models.associations import device_group_devices, team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.log import Log, LogSeen
from app.models.public_key import PublicKey
from app.models.team import Team
from app.models.user import User
from app.schemas.log import LogCreate, LogResponse, LogCountResponse
from app.services.email import send_device_log_notification
from app.services.permissions import get_device_encryption_keys_list

router = APIRouter(prefix="/logs", tags=["logs"])


SIGMA_LEVELS = {
    "informational": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}


def is_sufficient_severity(alert_severity: str | None, user_level: str) -> bool:
    if not alert_severity:
        return True
    alert_val = SIGMA_LEVELS.get(alert_severity.lower(), 1)
    user_val = SIGMA_LEVELS.get(user_level.lower(), 3)
    return alert_val >= user_val


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
    )
    db.add(log)
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
                    send_device_log_notification, u.email, device.name, device.id, log.severity
                )

    await db.commit()

    return log


@router.get("/unread-count")
async def get_unread_logs_count(
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
        return {"unread_count": 0}

    # If the user has configured minimal alert severity, count all with lower severity as read.
    sufficient_severities = []
    for sev, val in SIGMA_LEVELS.items():
        if val >= SIGMA_LEVELS.get(user.notification_level.lower(), 3):
            sufficient_severities.extend([sev, sev.upper(), sev.capitalize()])

    seen_subquery = select(LogSeen.log_id).where(LogSeen.user_id == user.id)
    if user.extended_edr_enabled:
        # Extended EDR: a log is "active" until it has an explicit resolution.
        # Seen status alone does not close an alert.
        count_query = select(func.count(Log.id)).where(
            Log.device_id.in_(visible_device_ids),
            or_(
                Log.alert_resolution.is_(None),
                Log.alert_resolution == "none"
            ),
            or_(
                Log.severity.is_(None),
                Log.severity.in_(sufficient_severities)
            )
        )
    else:
        # Basic mode: a log is "active" until the user has seen it.
        # Resolution is not required in basic mode — seeing the alert is enough.
        count_query = select(func.count(Log.id)).where(
            Log.device_id.in_(visible_device_ids),
            Log.id.not_in(seen_subquery),
            or_(
                Log.severity.is_(None),
                Log.severity.in_(sufficient_severities)
            )
        )
    unread_res = await db.execute(count_query)
    count = unread_res.scalar_one()
    return {"unread_count": count}


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
    logs_res = await db.execute(
        select(Log.id).where(Log.device_id.in_(visible_device_ids))
    )
    visible_log_ids = logs_res.scalars().all()

    if visible_log_ids:
        # Find which ones are already marked seen
        existing_res = await db.execute(
            select(LogSeen.log_id).where(
                LogSeen.user_id == user.id, LogSeen.log_id.in_(visible_log_ids)
            )
        )
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

    seen_result = await db.execute(
        select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id)
    )
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
        return LogCountResponse(total_count=0)

    if device_id:
        if device_id not in visible_device_ids:
            raise HTTPException(status_code=403, detail="No log permission for this device")
        query = select(func.count(Log.id)).where(Log.device_id == device_id)
    else:
        query = select(func.count(Log.id)).where(Log.device_id.in_(visible_device_ids))

    if from_time:
        query = query.where(Log.time >= ensure_utc(from_time).replace(tzinfo=None))
    if to_time:
        query = query.where(Log.time <= ensure_utc(to_time).replace(tzinfo=None))

    count_res = await db.execute(query)
    count = count_res.scalar_one()
    return LogCountResponse(total_count=count)


@router.get("/", response_model=list[LogResponse])
async def list_logs(
    device_id: int | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    page: int = 1,
    limit: int = 100,
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

    # Collect all device IDs user can see logs for
    visible_device_ids = set()
    for team in teams:
        for group in team.groups:
            for device in group.devices:
                visible_device_ids.add(device.id)

    if not visible_device_ids:
        return []

    offset = (page - 1) * limit
    
    if device_id:
        if device_id not in visible_device_ids:
            raise HTTPException(status_code=403, detail="No log permission for this device")
        query = select(Log).where(Log.device_id == device_id)
    else:
        query = select(Log).where(Log.device_id.in_(visible_device_ids))

    if from_time:
        query = query.where(Log.time >= ensure_utc(from_time).replace(tzinfo=None))
    if to_time:
        query = query.where(Log.time <= ensure_utc(to_time).replace(tzinfo=None))

    query = query.order_by(Log.time.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    seen_log_ids = set()
    if logs:
        log_ids = [log.id for log in logs]
        seen_res = await db.execute(
            select(LogSeen.log_id).where(LogSeen.user_id == user.id, LogSeen.log_id.in_(log_ids))
        )
        seen_log_ids = set(seen_res.scalars().all())

    response_logs = []
    for log in logs:
        seen = (log.id in seen_log_ids)

        response_logs.append(
            LogResponse(
                id=log.id,
                device_id=log.device_id,
                time=log.time,
                content=log.content,
                signature=log.signature,
                seen=seen,
                severity=log.severity,
                alert_resolution=log.alert_resolution,
                triage_note=log.triage_note
            )
        )
    return response_logs


from app.schemas.log import LogResolveRequest

@router.patch("/{log_id}/resolve", response_model=LogResponse)
async def resolve_log(
    log_id: int,
    data: LogResolveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Log).where(Log.id == log_id))
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
        seen_result = await db.execute(
            select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id)
        )
        if not seen_result.scalar_one_or_none():
            log_seen = LogSeen(user_id=user.id, log_id=log_id)
            db.add(log_seen)

    await db.commit()
    await db.refresh(log)

    # Determine seen status for response
    seen_result = await db.execute(
        select(LogSeen).where(LogSeen.user_id == user.id, LogSeen.log_id == log_id)
    )
    seen = seen_result.scalar_one_or_none() is not None

    return LogResponse(
        id=log.id,
        device_id=log.device_id,
        time=log.time,
        content=log.content,
        signature=log.signature,
        seen=seen,
        severity=log.severity,
        alert_resolution=log.alert_resolution,
        triage_note=log.triage_note
    )


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
