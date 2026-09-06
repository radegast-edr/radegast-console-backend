import asyncio
import logging
import random

from filelock import Timeout
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.database
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.email_bulk_state import EmailBulkState
from app.models.pack import Pack
from app.models.pack_enabled import PackEnabled
from app.models.queued_email import QueuedEmail
from app.models.team import Team
from app.models.user import User
from app.services.packs import delete_pack_files
from app.services.permissions import (
    get_team_members_transitive,
    has_device_admin_permission,
    has_group_admin_permission,
    has_team_admin_permission,
    has_team_pack_permission,
    mark_team_groups_refresh,
)
from app.utils import get_worker_lock, utc_now

logger = logging.getLogger(__name__)


async def check_user_can_be_deleted(user_id: int, db: AsyncSession) -> list[str]:
    """
    Check if a user can safely be deleted or scheduled for deletion.
    Unified Lifecycle Rules:
    - Keep: if another user can manage the entity.
    - Refuse: if another user needs the entity but cannot manage it.
    - Delete: if no other user needs or manages it.
    Returns a list of conflict reasons blocking deletion.
    """
    conflicts: list[str] = []

    # 1. Check Teams
    result_teams = await db.execute(select(Team).options(selectinload(Team.users)).where(Team.users.any(User.id == user_id)))
    user_teams = result_teams.scalars().all()

    res_all_teams = await db.execute(select(Team).options(selectinload(Team.users)))
    all_teams = res_all_teams.scalars().all()

    for team in all_teams:
        other_members = [u for u in team.users if u.id != user_id]
        if not other_members:
            continue

        if await has_team_admin_permission(team.id, user_id, db):
            transitive_members = await get_team_members_transitive(team.id, db)
            other_admin_ids = [uid for uid in transitive_members if uid != user_id and await has_team_admin_permission(team.id, uid, db)]
            if not other_admin_ids:
                conflicts.append(
                    f"You are the last admin of team '{team.name}', which has other members. "
                    "Please promote another member to admin or remove other members before deleting your account."
                )

    # 2. Check Device Groups
    # Find all device groups accessible via user's teams
    user_team_ids = [t.id for t in user_teams]
    if user_team_ids:
        result_groups = await db.execute(
            select(DeviceGroup)
            .options(
                selectinload(DeviceGroup.teams).selectinload(Team.users),
                selectinload(DeviceGroup.devices),
            )
            .where(DeviceGroup.teams.any(Team.id.in_(user_team_ids)))
        )
        groups = result_groups.scalars().all()

        for group in groups:
            # Check other users who have access to this group across all linked teams
            group_other_user_ids = set()
            for t in group.teams:
                transitive_members = await get_team_members_transitive(t.id, db)
                group_other_user_ids.update(u for u in transitive_members if u != user_id)

            if group_other_user_ids:
                # Other users need/use this group. Verify if at least one other user has admin on it.
                has_admin = False
                for other_uid in group_other_user_ids:
                    if await has_group_admin_permission(group.id, other_uid, db):
                        has_admin = True
                        break
                if not has_admin:
                    conflicts.append(
                        f"Device group '{group.name}' is shared with other users, but no other admin will remain to manage it."
                    )

    # 3. Check Devices
    # Find devices in user's groups
    if user_team_ids:
        result_devices = await db.execute(
            select(Device)
            .options(selectinload(Device.groups).selectinload(DeviceGroup.teams))
            .where(Device.groups.any(DeviceGroup.teams.any(Team.id.in_(user_team_ids))))
        )
        devices = result_devices.scalars().all()

        for device in devices:
            # Collect other users who access this device via any group
            device_other_user_ids = set()
            for g in device.groups:
                for t in g.teams:
                    t_members = await get_team_members_transitive(t.id, db)
                    device_other_user_ids.update(u for u in t_members if u != user_id)

            if device_other_user_ids:
                has_admin = False
                for other_uid in device_other_user_ids:
                    if await has_device_admin_permission(device.id, other_uid, db):
                        has_admin = True
                        break
                if not has_admin:
                    conflicts.append(f"Device '{device.name}' is monitored by other users, but no other admin will remain to manage it.")

    # 4. Check Packs
    # Find private packs linked to user's teams
    if user_team_ids:
        result_packs = await db.execute(
            select(Pack)
            .options(
                selectinload(Pack.teams).selectinload(Team.users),
                selectinload(Pack.versions),
            )
            .where(Pack.teams.any(Team.id.in_(user_team_ids)))
        )
        packs = result_packs.scalars().all()

        for pack in packs:
            # Check if user has pack write permission on this pack
            user_has_write = False
            for t in pack.teams:
                if t.id in user_team_ids and await has_team_pack_permission(t.id, user_id, db):
                    user_has_write = True
                    break

            if user_has_write:
                # Check if other users need this pack:
                # A) Pack is linked to teams with other members
                pack_other_user_ids = set()
                for t in pack.teams:
                    t_members = await get_team_members_transitive(t.id, db)
                    pack_other_user_ids.update(u for u in t_members if u != user_id)

                # B) Pack is enabled on device groups accessed by other users
                pack_version_ids = [v.id for v in pack.versions]
                if pack_version_ids:
                    res_pe = await db.execute(
                        select(PackEnabled)
                        .options(selectinload(PackEnabled.device_group).selectinload(DeviceGroup.teams))
                        .where(PackEnabled.pack_version_id.in_(pack_version_ids))
                    )
                    for pe in res_pe.scalars().all():
                        if pe.device_group:
                            for gt in pe.device_group.teams:
                                gt_members = await get_team_members_transitive(gt.id, db)
                                pack_other_user_ids.update(u for u in gt_members if u != user_id)

                if pack_other_user_ids:
                    # Other users need this pack. Verify if another user has pack write permission.
                    other_has_write = False
                    for other_uid in pack_other_user_ids:
                        for t in pack.teams:
                            if await has_team_pack_permission(t.id, other_uid, db):
                                other_has_write = True
                                break
                        if other_has_write:
                            break

                    if not other_has_write:
                        conflicts.append(
                            f"Pack '{pack.name}' is used by other users or teams, but no other user has write permission to manage it."
                        )

    return conflicts


async def perform_user_deletion(user_id: int, db: AsyncSession) -> None:
    """
    Permanently delete a user and clean up / prune unneeded resources according to Unified Lifecycle Rules:
    - Keep: if another user can manage the entity.
    - Delete: if no other user needs or manages it.
    """
    conflicts = await check_user_can_be_deleted(user_id, db)
    if conflicts:
        raise ValueError(f"Cannot delete user {user_id}: {'; '.join(conflicts)}")

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.teams).selectinload(Team.users),
            selectinload(User.teams).selectinload(Team.groups).selectinload(DeviceGroup.devices),
            selectinload(User.teams).selectinload(Team.packs),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        return

    user_teams = list(user.teams)

    # 1. Process teams
    teams_to_delete = []
    teams_to_keep = []

    for team in user_teams:
        other_members = [u for u in team.users if u.id != user_id]
        if not other_members:
            teams_to_delete.append(team)
        else:
            teams_to_keep.append(team)

    # 2. For kept teams, remove user and refresh group keys
    for team in teams_to_keep:
        team.users.remove(user)
        await mark_team_groups_refresh(team.id, db)

    # 3. Identify orphaned device groups linked to deleted teams
    deleted_team_ids = {t.id for t in teams_to_delete}
    groups_to_delete: set[DeviceGroup] = set()

    for team in teams_to_delete:
        for group in team.groups:
            # Check if this group is linked to any remaining team not being deleted
            res_grp = await db.execute(select(DeviceGroup).options(selectinload(DeviceGroup.teams)).where(DeviceGroup.id == group.id))
            g_obj = res_grp.scalar_one_or_none()
            if g_obj:
                remaining_teams = [t for t in g_obj.teams if t.id not in deleted_team_ids]
                if not remaining_teams:
                    groups_to_delete.add(g_obj)

    # 4. Check devices in groups to delete
    # If a device belongs only to groups being deleted, delete the device
    deleted_group_ids = {g.id for g in groups_to_delete}
    devices_to_delete: set[Device] = set()

    for group in groups_to_delete:
        for device in group.devices:
            res_dev = await db.execute(select(Device).options(selectinload(Device.groups)).where(Device.id == device.id))
            d_obj = res_dev.scalar_one_or_none()
            if d_obj:
                remaining_groups = [g for g in d_obj.groups if g.id not in deleted_group_ids]
                if not remaining_groups:
                    devices_to_delete.add(d_obj)

    # 5. Check private packs linked to deleted teams
    packs_to_delete: set[Pack] = set()
    for team in teams_to_delete:
        for pack in team.packs:
            res_pk = await db.execute(select(Pack).options(selectinload(Pack.teams), selectinload(Pack.versions)).where(Pack.id == pack.id))
            pk_obj = res_pk.scalar_one_or_none()
            if pk_obj:
                remaining_teams = [t for t in pk_obj.teams if t.id not in deleted_team_ids]
                if not remaining_teams:
                    # Check if enabled on any remaining group
                    pack_v_ids = [v.id for v in pk_obj.versions]
                    res_pe = await db.execute(
                        select(PackEnabled).where(
                            PackEnabled.pack_version_id.in_(pack_v_ids),
                            ~PackEnabled.device_group_id.in_(deleted_group_ids) if deleted_group_ids else True,
                        )
                    )
                    if not res_pe.scalars().all():
                        packs_to_delete.add(pk_obj)

    # Execute deletion of orphaned packs
    for pack in packs_to_delete:
        delete_pack_files(pack.id)
        await db.delete(pack)

    # Clear creator_id on any packs created by this user that are kept
    res_created_packs = await db.execute(select(Pack).where(Pack.creator_id == user.id))
    for p in res_created_packs.scalars().all():
        if p not in packs_to_delete:
            p.creator_id = None

    # Execute deletion of orphaned devices (cascades logs)
    for device in devices_to_delete:
        await db.delete(device)

    # Execute deletion of orphaned groups (cascades exclusions and pack_enabled)
    for group in groups_to_delete:
        await db.delete(group)

    # Execute deletion of solo teams
    for team in teams_to_delete:
        await db.delete(team)

    # Delete queued emails and bulk state for this user's email
    await db.execute(delete(QueuedEmail).where(QueuedEmail.email_to == user.email))
    await db.execute(delete(EmailBulkState).where(EmailBulkState.email_to == user.email))

    # Delete the user (cascades public_keys, hardware_tokens, api_keys, key_transfers, team_invitations, logs_seen)
    await db.delete(user)
    await db.commit()
    logger.info(f"Successfully deleted user ID {user_id} and pruned orphaned resources.")


async def process_account_deletions() -> None:
    """Check for users whose account deletion grace period has expired and delete them."""
    now = utc_now()
    async with app.database.async_session() as session:
        result = await session.execute(
            select(User.id).where(
                User.deletion_scheduled_at.isnot(None),
                User.deletion_scheduled_at <= now,
            )
        )
        expired_user_ids = result.scalars().all()

        for uid in expired_user_ids:
            try:
                await perform_user_deletion(uid, session)
            except Exception as e:
                logger.error(f"Failed to process scheduled account deletion for user {uid}: {e}")


async def process_account_deletions_loop() -> None:
    """Background worker loop checking for expired deletion grace periods."""
    lock = get_worker_lock()
    while True:
        try:
            async with lock:
                await process_account_deletions()
        except Timeout:
            pass
        except Exception as e:
            logger.error(f"[ACCOUNT DELETION WORKER ERROR] {e}")
        await asyncio.sleep(random.randint(60, 300))  # noqa: S311
