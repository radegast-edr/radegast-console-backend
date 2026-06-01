from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, func
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
from app.schemas.log import LogCreate, LogResponse
from app.services.email import send_device_log_notification

router = APIRouter(prefix="/logs", tags=["logs"])


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
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    # Notify users with log-read permission on this device who opted in
    notif_result = await db.execute(
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
            User.notify_device_log.is_(True),
        )
        .distinct()
    )
    for u in notif_result.scalars().all():
        background_tasks.add_task(send_device_log_notification, u.email, device.name, device.id)

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

    seen_subquery = select(LogSeen.log_id).where(LogSeen.user_id == user.id)
    count_query = select(func.count(Log.id)).where(
        Log.device_id.in_(visible_device_ids),
        Log.id.not_in(seen_subquery)
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


@router.get("/", response_model=list[LogResponse])
async def list_logs(
    device_id: int | None = None,
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
    query = select(Log).where(Log.device_id.in_(visible_device_ids)).order_by(Log.time.desc()).offset(offset).limit(limit)
    if device_id:
        if device_id not in visible_device_ids:
            raise HTTPException(status_code=403, detail="No log permission for this device")
        query = select(Log).where(Log.device_id == device_id).order_by(Log.time.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    seen_log_ids = set()
    if logs:
        log_ids = [log.id for log in logs]
        seen_res = await db.execute(
            select(LogSeen.log_id).where(LogSeen.user_id == user.id, LogSeen.log_id.in_(log_ids))
        )
        seen_log_ids = set(seen_res.scalars().all())

    return [
        LogResponse(
            id=log.id,
            device_id=log.device_id,
            time=log.time,
            content=log.content,
            signature=log.signature,
            seen=log.id in seen_log_ids
        )
        for log in logs
    ]


@router.get("/encryption-keys")
async def get_encryption_keys(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """Returns all public keys of users with log read permission for this device's groups."""
    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device.id)
    )
    device = result.scalar_one()

    from app.services.permissions import get_team_members_transitive
    user_ids = set()
    for group in device.groups:
        for team in group.teams:
            if team.permission_logs == "read":
                team_user_ids = await get_team_members_transitive(team.id, db)
                user_ids.update(team_user_ids)

    if not user_ids:
        return []

    result = await db.execute(
        select(PublicKey).where(PublicKey.user_id.in_(list(user_ids)))
    )
    keys = result.scalars().all()
    return [{"user_id": k.user_id, "public_key": k.public_key, "key_type": k.key_type} for k in keys]
