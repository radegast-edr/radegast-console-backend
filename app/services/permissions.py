from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.associations import team_users
from app.models.team import Team


async def get_user_team_ids_transitive(user_id: int, db: AsyncSession) -> set[int]:
    """Get all team IDs where the user is a direct or virtual member (via managing teams)."""
    # 1. Get direct team IDs
    res = await db.execute(select(team_users.c.team_id).where(team_users.c.user_id == user_id))
    direct_ids = {row[0] for row in res.all()}

    # 2. Expand transitively down the managing chain
    visited = set(direct_ids)
    queue = list(direct_ids)

    while queue:
        next_queue = []
        for team_id in queue:
            # Find all teams managed by team_id
            res_sub = await db.execute(select(Team.id).where(Team.managing_team_id == team_id))
            for subteam_id in res_sub.scalars().all():
                if subteam_id not in visited:
                    visited.add(subteam_id)
                    next_queue.append(subteam_id)
        queue = next_queue

    return visited


async def is_user_member_of_team_transitive(team_id: int, user_id: int, db: AsyncSession) -> bool:
    """Check if the user is a member of the team directly or via managing team chain."""
    user_team_ids = await get_user_team_ids_transitive(user_id, db)
    return team_id in user_team_ids


async def has_team_admin_permission(team_id: int, user_id: int, db: AsyncSession) -> bool:
    """
    Check if user has admin permission on team_id.
    This checks if the team itself has admin=write and the user is a member,
    or transitively if any managing team in the chain has admin=write and the user is a member.
    """
    user_team_ids = await get_user_team_ids_transitive(user_id, db)

    # Walk up the managing chain of team_id
    curr_id = team_id
    visited = set()
    while curr_id is not None and curr_id not in visited:
        visited.add(curr_id)

        # Load team details for this node in the chain
        res = await db.execute(select(Team).where(Team.id == curr_id))
        team = res.scalar_one_or_none()
        if not team:
            break

        # If this team in the chain has admin permission, and the user is a member (direct or virtual) of this team:
        if team.permission_admin == "write" and curr_id in user_team_ids:
            return True

        curr_id = team.managing_team_id

    return False


async def get_team_members_transitive(team_id: int, db: AsyncSession) -> set[int]:
    """Get all users who are direct or virtual members of a team (i.e. members of it or its managing teams)."""
    # Find all teams in the managing chain (team_id, and all teams that manage it)
    chain_ids = set()
    curr_id = team_id
    visited = set()
    while curr_id is not None and curr_id not in visited:
        visited.add(curr_id)
        chain_ids.add(curr_id)
        res = await db.execute(select(Team.managing_team_id).where(Team.id == curr_id))
        curr_id = res.scalar()

    if not chain_ids:
        return set()

    # Get all users of any team in the chain
    res_users = await db.execute(select(team_users.c.user_id).where(team_users.c.team_id.in_(list(chain_ids))))
    return {row[0] for row in res_users.all()}


async def has_device_admin_permission(device_id: int, user_id: int, db: AsyncSession) -> bool:
    """Check transitively if the user has admin permission on the device (by checking its groups/teams)."""
    from sqlalchemy.orm import selectinload

    from app.models.device import Device
    from app.models.device_group import DeviceGroup

    result = await db.execute(
        select(Device).options(selectinload(Device.groups).selectinload(DeviceGroup.teams)).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        return False

    has_access = False
    for group in device.groups:
        for team in group.teams:
            if await has_team_admin_permission(team.id, user_id, db):
                has_access = True
                break
        if has_access:
            break
    return has_access


async def has_team_pack_permission(team_id: int, user_id: int, db: AsyncSession) -> bool:
    """
    Check if user has pack write permission on team_id.
    This checks if the team itself has permission_pack=write and the user is a member,
    or transitively if any managing team in the chain has permission_pack=write and the user is a member.
    """
    user_team_ids = await get_user_team_ids_transitive(user_id, db)

    # Walk up the managing chain of team_id
    curr_id = team_id
    visited = set()
    while curr_id is not None and curr_id not in visited:
        visited.add(curr_id)

        # Load team details for this node in the chain
        res = await db.execute(select(Team).where(Team.id == curr_id))
        team = res.scalar_one_or_none()
        if not team:
            break

        # If this team in the chain has pack permission, and the user is a member (direct or virtual) of this team:
        if team.permission_pack == "write" and curr_id in user_team_ids:
            return True

        curr_id = team.managing_team_id

    return False


async def get_device_encryption_keys_list(device_id: int, db: AsyncSession) -> list[dict]:
    """
    Shared utility: returns all public keys of users who have log-read permission
    for the given device (via any group/team chain).
    This is used both by the device-facing and user-facing encryption-key endpoints.
    """
    from app.models.device import Device
    from app.models.device_group import DeviceGroup
    from app.models.public_key import PublicKey

    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        return []

    user_ids: set[int] = set()
    for group in device.groups:
        for team in group.teams:
            if team.permission_logs == "read":
                team_user_ids = await get_team_members_transitive(team.id, db)
                user_ids.update(team_user_ids)

    if not user_ids:
        return []

    result = await db.execute(select(PublicKey).where(PublicKey.user_id.in_(list(user_ids))))
    keys = result.scalars().all()
    return [{"user_id": k.user_id, "public_key": k.public_key, "key_type": k.key_type} for k in keys]


async def get_group_recipient_public_keys(group_id: int, db: AsyncSession, exclude_user_id: int | None = None) -> list[str]:
    """
    Returns a list of all recipient public keys (both users' regular/secondary keys and devices' encryption public keys)
    for a given device group.
    """
    from app.models.device_group import DeviceGroup
    from app.models.public_key import PublicKey
    from app.models.team import Team

    result = await db.execute(
        select(DeviceGroup)
        .options(
            selectinload(DeviceGroup.teams).selectinload(Team.users),
            selectinload(DeviceGroup.devices),
        )
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        return []

    pub_keys: set[str] = set()

    # Add devices' encryption public keys
    for device in group.devices:
        if device.encryption_public_key:
            pub_keys.add(device.encryption_public_key)

    # Add users' public keys (non-recovery)
    user_ids = set()
    for team in group.teams:
        for user in team.users:
            if exclude_user_id is None or user.id != exclude_user_id:
                user_ids.add(user.id)

    if user_ids:
        res = await db.execute(select(PublicKey).where(PublicKey.user_id.in_(list(user_ids)), PublicKey.key_type != "recovery"))
        for pk in res.scalars().all():
            pub_keys.add(pk.public_key)

    return list(pub_keys)
