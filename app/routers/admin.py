from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.device import Device
from app.models.log import Log
from app.models.pack import Pack
from app.models.user import User, UserRole
from app.schemas.device import DeviceResponse
from app.schemas.pack import PackResponse
from app.schemas.user import UserResponse
from app.services.packs import delete_pack_files
from app.utils import utc_now


class AdminAlertStatsResponse(BaseModel):
    severity_distribution: dict[str, int]
    rule_distribution: dict[str, int]


class AdminDeviceStatsResponse(BaseModel):
    agent_distribution: dict[str, int]
    rustinel_distribution: dict[str, int]


router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(user: User):
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("/users", response_model=list[UserResponse])
async def list_all_users(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(User).options(selectinload(User.hardware_tokens), selectinload(User.public_keys)))
    users = result.scalars().all()

    from app.config import settings
    from app.dependencies import user_has_required_mfa_setup

    response_users = []
    for u in users:
        req_level = "none"
        if u.role.value == "admin":
            req_level = settings.mfa_required_level_admin
        elif u.role.value == "maintainer":
            req_level = settings.mfa_required_level_maintainer
        elif u.role.value == "user":
            req_level = settings.mfa_required_level_user

        conf_level = "none"
        if len(u.hardware_tokens) > 0:
            conf_level = "hardware_token"
        elif u.otp_enabled and u.otp_secret:
            conf_level = "otp"

        setup_missing = not user_has_required_mfa_setup(u, req_level)

        response_users.append(
            UserResponse(
                id=u.id,
                email=u.email,
                role=u.role.value if hasattr(u.role, "value") else str(u.role),
                verified=u.verified,
                has_keys=len(u.public_keys) > 0,
                mfa_required_level=req_level,
                mfa_setup_missing=setup_missing,
                mfa_configured_level=conf_level,
                extended_edr_enabled=u.extended_edr_enabled,
                api_keys_enabled=u.api_keys_enabled,
            )
        )
    return response_users


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    if user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(target)
    await db.commit()
    return {"message": "User deleted"}


@router.get("/devices", response_model=list[DeviceResponse])
async def list_all_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Device))
    return result.scalars().all()


@router.delete("/devices/{device_id}")
async def admin_delete_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await db.delete(device)
    await db.commit()
    return {"message": "Device deleted"}


@router.get("/packs", response_model=list[PackResponse])
async def list_all_packs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Pack).options(selectinload(Pack.teams)))
    return result.scalars().all()


@router.delete("/packs/{pack_id}")
async def admin_delete_pack(
    pack_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    result = await db.execute(select(Pack).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    delete_pack_files(pack_id)
    await db.delete(pack)
    await db.commit()
    return {"message": "Pack deleted"}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    result = await db.execute(select(User).options(selectinload(User.hardware_tokens)).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    new_password = "".join(secrets.choice(alphabet) for _ in range(12))

    from app.services.auth import hash_password

    target.password = hash_password(new_password)
    target.password_change = utc_now()

    target.otp_enabled = False
    target.otp_secret = None
    target.hardware_tokens = []

    await db.commit()

    from app.services.email import send_password_reset_email

    background_tasks.add_task(send_password_reset_email, target.email, new_password)

    return {"message": "User password reset successfully and MFA cleared"}


@router.get("/stats/alerts", response_model=AdminAlertStatsResponse)
async def get_admin_alert_stats(
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    from app.utils import ensure_utc

    # Severity distribution query
    query_sev = select(Log.severity, func.count(Log.id)).group_by(Log.severity)
    if from_time:
        query_sev = query_sev.where(Log.time >= ensure_utc(from_time).replace(tzinfo=None))
    if to_time:
        query_sev = query_sev.where(Log.time <= ensure_utc(to_time).replace(tzinfo=None))
    res_sev = await db.execute(query_sev)
    severity_distribution = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): row[1] for row in res_sev.all() if row[0] is not None
    }

    # Also handle None severity if any
    where_clauses = [Log.severity.is_(None)]
    if from_time:
        where_clauses.append(Log.time >= ensure_utc(from_time).replace(tzinfo=None))
    if to_time:
        where_clauses.append(Log.time <= ensure_utc(to_time).replace(tzinfo=None))

    from sqlalchemy import and_

    res_sev_none = await db.execute(select(func.count(Log.id)).where(and_(*where_clauses)))
    none_count = res_sev_none.scalar() or 0
    if none_count > 0:
        severity_distribution["unknown"] = none_count

    # Rule distribution query
    query_rule = select(Log.rule_id, func.count(Log.id)).group_by(Log.rule_id)
    if from_time:
        query_rule = query_rule.where(Log.time >= ensure_utc(from_time).replace(tzinfo=None))
    if to_time:
        query_rule = query_rule.where(Log.time <= ensure_utc(to_time).replace(tzinfo=None))
    res_rule = await db.execute(query_rule)
    rule_distribution = {row[0] or "unknown": row[1] for row in res_rule.all()}

    return AdminAlertStatsResponse(
        severity_distribution=severity_distribution,
        rule_distribution=rule_distribution,
    )


@router.get("/stats/devices", response_model=AdminDeviceStatsResponse)
async def get_admin_device_stats(
    exclude_offline: bool = False,
    exclude_no_version: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    result = await db.execute(select(Device))
    devices = result.scalars().all()

    agent_distribution = {}
    rustinel_distribution = {}

    now_utc = datetime.now(UTC)
    ten_minutes_ago = now_utc - timedelta(minutes=10)

    for d in devices:
        if exclude_offline:
            if not d.last_seen:
                continue
            last_seen_val = d.last_seen
            if last_seen_val.tzinfo is None:
                last_seen_val = last_seen_val.replace(tzinfo=UTC)
            if last_seen_val < ten_minutes_ago:
                continue

        agent_ver = d.agent_version
        if exclude_no_version and (not agent_ver or agent_ver.strip() == ""):
            pass
        else:
            ver_key = agent_ver if (agent_ver and agent_ver.strip() != "") else "unknown"
            agent_distribution[ver_key] = agent_distribution.get(ver_key, 0) + 1

        rustinel_ver = d.rustinel_version
        if exclude_no_version and (not rustinel_ver or rustinel_ver.strip() == ""):
            pass
        else:
            ver_key = rustinel_ver if (rustinel_ver and rustinel_ver.strip() != "") else "unknown"
            rustinel_distribution[ver_key] = rustinel_distribution.get(ver_key, 0) + 1

    return AdminDeviceStatsResponse(
        agent_distribution=agent_distribution,
        rustinel_distribution=rustinel_distribution,
    )


class AdminBroadcastRequest(BaseModel):
    subject: str
    html_body: str
    email_type: Literal["downtime_maintenance", "news_updates"]


@router.post("/broadcast")
async def send_admin_broadcast(
    data: AdminBroadcastRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    # Find the matching users based on subscription preference
    if data.email_type == "downtime_maintenance":
        query = select(User).where(User.notify_downtime_maintenance)
    elif data.email_type == "news_updates":
        query = select(User).where(User.notify_news_updates)
    else:
        raise HTTPException(status_code=400, detail="Invalid email type")

    result = await db.execute(query)
    recipient_users = result.scalars().all()

    from app.services.email import send_email

    now = utc_now()
    for i, u in enumerate(recipient_users):
        wave_index = i // 20
        scheduled_at = now + timedelta(minutes=wave_index)
        await send_email(
            to=u.email,
            subject=data.subject,
            html_body=data.html_body,
            email_type=data.email_type,
            scheduled_at=scheduled_at,
        )

    return {"message": f"Successfully queued broadcast to {len(recipient_users)} users."}
