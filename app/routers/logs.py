from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.log import Log
from app.models.public_key import PublicKey
from app.models.team import Team
from app.models.user import User
from app.schemas.log import LogCreate, LogResponse

router = APIRouter(prefix="/logs", tags=["logs"])


@router.post("/", response_model=LogResponse)
async def submit_log(
    data: LogCreate,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    log = Log(
        device_id=device.id,
        time=data.time,
        content=data.content,
        signature=data.signature,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


@router.get("/", response_model=list[LogResponse])
async def list_logs(
    device_id: int | None = None,
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

    query = select(Log).where(Log.device_id.in_(visible_device_ids)).order_by(Log.time.desc()).limit(100)
    if device_id:
        if device_id not in visible_device_ids:
            raise HTTPException(status_code=403, detail="No log permission for this device")
        query = select(Log).where(Log.device_id == device_id).order_by(Log.time.desc()).limit(100)

    result = await db.execute(query)
    return result.scalars().all()


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

    user_ids = set()
    for group in device.groups:
        for team in group.teams:
            if team.permission_logs == "read":
                for u in team.users:
                    user_ids.add(u.id)

    if not user_ids:
        return []

    result = await db.execute(
        select(PublicKey).where(PublicKey.user_id.in_(user_ids))
    )
    keys = result.scalars().all()
    return [{"user_id": k.user_id, "public_key": k.public_key, "key_type": k.key_type} for k in keys]
