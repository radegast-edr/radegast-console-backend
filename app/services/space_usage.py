import asyncio
import logging

from filelock import Timeout
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.database
from app.config import settings
from app.models.associations import device_group_devices, team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.team import PermissionAdmin, Team
from app.models.user import User
from app.utils import get_worker_lock

logger = logging.getLogger(__name__)


async def compute_device_group_space_usage(session: AsyncSession) -> None:
    """
    Compute space usage for each device group as the sum of total_space_used of all devices belonging to it.
    """
    res = await session.execute(
        select(
            device_group_devices.c.device_group_id,
            func.coalesce(func.sum(Device.total_space_used), 0),
        )
        .join(Device, Device.id == device_group_devices.c.device_id)
        .group_by(device_group_devices.c.device_group_id)
    )
    group_usage_map = dict(res.all())

    res_groups = await session.execute(select(DeviceGroup.id))
    all_group_ids = res_groups.scalars().all()

    for gid in all_group_ids:
        usage = int(group_usage_map.get(gid, 0))
        await session.execute(update(DeviceGroup).where(DeviceGroup.id == gid).values(total_space_used=usage))


def get_descendant_team_ids(root_team_id: int, parent_to_children: dict[int, list[int]]) -> set[int]:
    """Return all team IDs managed directly or transitively by root_team_id, including root_team_id itself."""
    descendants = {root_team_id}
    queue = [root_team_id]
    while queue:
        curr = queue.pop(0)
        for child in parent_to_children.get(curr, []):
            if child not in descendants:
                descendants.add(child)
                queue.append(child)
    return descendants


async def compute_team_space_usage(session: AsyncSession) -> None:
    """
    Compute space usage for all root teams (teams with admin=write permission and no managing team set).
    If a team has a managing team set, its storage space is taken by its managing team (rolled up transitively),
    and the managed team's total_space_used is set to 0.
    A single device belonging to multiple groups of the team family is counted only once.
    """
    res_teams = await session.execute(select(Team))
    all_teams = res_teams.scalars().all()

    parent_to_children: dict[int, list[int]] = {}
    for t in all_teams:
        if t.managing_team_id is not None:
            parent_to_children.setdefault(t.managing_team_id, []).append(t.id)

    for team in all_teams:
        is_admin_write = team.permission_admin in (PermissionAdmin.write, "write")
        is_root = team.managing_team_id is None

        if not (is_admin_write and is_root):
            await session.execute(update(Team).where(Team.id == team.id).values(total_space_used=0))
            continue

        family_team_ids = get_descendant_team_ids(team.id, parent_to_children)

        res_devices = await session.execute(
            select(func.coalesce(func.sum(Device.total_space_used), 0)).where(
                Device.id.in_(
                    select(device_group_devices.c.device_id)
                    .join(
                        team_device_groups,
                        team_device_groups.c.device_group_id == device_group_devices.c.device_group_id,
                    )
                    .where(team_device_groups.c.team_id.in_(family_team_ids))
                )
            )
        )
        team_usage = int(res_devices.scalar() or 0)
        await session.execute(update(Team).where(Team.id == team.id).values(total_space_used=team_usage))


async def compute_user_space_usage(session: AsyncSession) -> None:
    """
    Compute user quota as the sum of all devices belonging to qualifying root teams
    (teams with admin=write permission and no managing team set) that the user is a member of.
    If a team has a managing team, only the root managing team takes the storage space.
    Devices are deduplicated across all groups and descendant teams.
    Users who are not members of any qualifying root team will have total_space_used = 0.
    """
    res_teams = await session.execute(select(Team))
    all_teams = res_teams.scalars().all()

    parent_to_children: dict[int, list[int]] = {}
    qualifying_root_team_ids: set[int] = set()

    for t in all_teams:
        if t.managing_team_id is not None:
            parent_to_children.setdefault(t.managing_team_id, []).append(t.id)
        elif t.permission_admin in (PermissionAdmin.write, "write"):
            qualifying_root_team_ids.add(t.id)

    res_users = await session.execute(select(User.id))
    all_user_ids = res_users.scalars().all()

    for user_id in all_user_ids:
        res_user_teams = await session.execute(
            select(team_users.c.team_id).where(
                team_users.c.user_id == user_id,
                team_users.c.team_id.in_(qualifying_root_team_ids),
            )
        )
        user_root_team_ids = res_user_teams.scalars().all()

        if not user_root_team_ids:
            await session.execute(update(User).where(User.id == user_id).values(total_space_used=0))
            continue

        user_family_team_ids: set[int] = set()
        for rt_id in user_root_team_ids:
            user_family_team_ids.update(get_descendant_team_ids(rt_id, parent_to_children))

        res_user_devices = await session.execute(
            select(func.coalesce(func.sum(Device.total_space_used), 0)).where(
                Device.id.in_(
                    select(device_group_devices.c.device_id)
                    .join(
                        team_device_groups,
                        team_device_groups.c.device_group_id == device_group_devices.c.device_group_id,
                    )
                    .where(team_device_groups.c.team_id.in_(user_family_team_ids))
                )
            )
        )
        user_usage = int(res_user_devices.scalar() or 0)
        await session.execute(update(User).where(User.id == user_id).values(total_space_used=user_usage))


async def compute_all_space_usage(session: AsyncSession | None = None) -> None:
    """
    Compute all space usages in sequence:
    1. Device groups
    2. Qualifying teams
    3. Users
    """
    if session is not None:
        await compute_device_group_space_usage(session)
        await compute_team_space_usage(session)
        await compute_user_space_usage(session)
        await session.commit()
    else:
        async with app.database.async_session() as s:
            await compute_device_group_space_usage(s)
            await compute_team_space_usage(s)
            await compute_user_space_usage(s)
            await s.commit()


async def process_space_usage_loop() -> None:
    """Background worker loop periodically calculating space usages."""
    lock = get_worker_lock()
    while True:
        try:
            async with lock:
                await compute_all_space_usage()
        except Timeout:
            pass
        except Exception as e:
            logger.error(f"[SPACE USAGE WORKER ERROR] {e}")
        await asyncio.sleep(max(1, settings.space_usage_interval_minutes) * 60)
