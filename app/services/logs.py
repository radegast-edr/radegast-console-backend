from datetime import datetime
from typing import TypeVar
from types import MappingProxyType

from sqlalchemy import select, Select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import User, Team, DeviceGroup
from app.models.log import LogSeverity, Log
from app.utils import ensure_utc

SEVERITY_TO_INT: MappingProxyType[LogSeverity, int] = MappingProxyType({
    LogSeverity.informational: 1,
    LogSeverity.low: 2,
    LogSeverity.medium: 3,
    LogSeverity.high: 4,
    LogSeverity.critical: 5,
})

T = TypeVar("T")


def is_sufficient_severity(alert_severity: LogSeverity | None, user_level: LogSeverity) -> bool:
    if alert_severity is None:
        return True  # Always include logs that are missing severity
    alert_val = SEVERITY_TO_INT[alert_severity]
    user_val = SEVERITY_TO_INT[user_level]
    return alert_val >= user_val


async def get_visible_device_ids(user: User, db: AsyncSession) -> set[int]:
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
    return visible_device_ids


def filter_logs(
    query: Select[T],
    from_time: datetime | None,
    to_time: datetime | None,
    min_level: LogSeverity | None,
    user: User,
) -> Select[T]:
    if from_time:
        query = query.where(Log.time >= ensure_utc(from_time).replace(tzinfo=None))
    if to_time:
        query = query.where(Log.time <= ensure_utc(to_time).replace(tzinfo=None))
    if not min_level:
        min_level = user.notification_level
    sufficient_severities: list[LogSeverity | None] = [None]  # Always include alerts that are missing severity
    sufficient_severities.extend(severity for severity in LogSeverity if is_sufficient_severity(severity, min_level))
    query = query.where(or_(Log.severity.is_(None), Log.severity.in_(sufficient_severities)))
    return query
