"""Shared logic for reusable exclusions: permissions, E2EE checks, last-change tracking and responses."""

from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.exclusion import Exclusion
from app.models.log import Log
from app.models.user import User
from app.schemas.exclusion import ExclusionGroupRef, ExclusionLastChange, ExclusionResponse
from app.services.permissions import get_user_team_ids_transitive, has_team_pack_permission

AGE_ARMOR_PREFIX = "-----BEGIN AGE ENCRYPTED FILE-----"
MIN_E2EE_PYTHON_AGENT = (0, 5)

# Eager-load options needed to evaluate permissions and build responses.
EXCLUSION_LOAD_OPTIONS = (
    selectinload(Exclusion.device_groups).selectinload(DeviceGroup.teams),
    selectinload(Exclusion.updated_by),
)


def agent_version_supports_e2ee(version: str | None) -> bool:
    """Mirror of the frontend check: unknown versions and python agents older than 0.5.0 are unsupported."""
    if not version:
        return False
    trimmed = version.strip()
    if not trimmed or trimmed.lower() == "unknown":
        return False
    if trimmed.startswith("python "):
        parts = trimmed[len("python ") :].strip().split(".")
        if len(parts) < 3:
            return False
        try:
            major, minor = int(parts[0]), int(parts[1])
        except ValueError:
            return False
        return (major, minor) >= MIN_E2EE_PYTHON_AGENT
    return True


async def ensure_groups_support_e2ee(groups: Iterable[DeviceGroup], db: AsyncSession) -> None:
    """Raise 400 if an encrypted exclusion cannot be applied to one of the groups."""
    for group in groups:
        if not group.public_key or not group.private_key:
            raise HTTPException(
                status_code=400,
                detail=f"Device group '{group.name}' has no encryption keys set up; encrypted exclusions cannot be applied",
            )
        result = await db.execute(
            select(Device.name, Device.agent_version).where(Device.groups.any(DeviceGroup.id == group.id), Device.deleted_at.is_(None))
        )
        for device_name, agent_version in result.all():
            if not agent_version_supports_e2ee(agent_version):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Device '{device_name}' in group '{group.name}' does not support encrypted exclusions "
                        "(agent version unknown or lower than python 0.5.0)"
                    ),
                )


def ensure_ciphertext(encrypted: bool, name: str | None, jsonata_query: str | None, description: str | None) -> None:
    """For encrypted exclusions, require AGE-armored content so plaintext is never stored by mistake."""
    if not encrypted:
        return
    for field, value in (("name", name), ("jsonata_query", jsonata_query)):
        if not value or not value.lstrip().startswith(AGE_ARMOR_PREFIX):
            raise HTTPException(status_code=400, detail=f"Encrypted exclusion field '{field}' must be AGE ciphertext")
    if description and not description.lstrip().startswith(AGE_ARMOR_PREFIX):
        raise HTTPException(status_code=400, detail="Encrypted exclusion field 'description' must be AGE ciphertext")


def ensure_soft_allowed(exclusion_type: str | None, user: User) -> None:
    if exclusion_type == "soft" and not user.extended_edr_enabled:
        raise HTTPException(status_code=400, detail="Soft exclusions are only available in extended EDR mode")


def ensure_version(exclusion: Exclusion, version: int) -> None:
    if exclusion.version != version:
        raise HTTPException(
            status_code=409,
            detail="The exclusion was changed by someone else. Reload it and try again.",
        )


async def ensure_alert_in_groups(alert_id: int | None, group_ids: Iterable[int], db: AsyncSession) -> None:
    """The source alert must come from a device that is a member of one of the exclusion's groups."""
    if alert_id is None:
        return
    ids = list(group_ids)
    result = await db.execute(
        select(Log.id).join(Device, Device.id == Log.device_id).where(Log.id == alert_id, Device.groups.any(DeviceGroup.id.in_(ids)))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=400, detail="The alert does not belong to a device in the selected groups")


def record_change(exclusion: Exclusion, action: str, user_id: int | None, group_id: int | None = None) -> None:
    exclusion.last_action = action
    exclusion.updated_at = datetime.now(UTC)
    exclusion.updated_by_user_id = user_id
    exclusion.last_action_group_id = group_id


class ExclusionAccess:
    """Caches the user's team membership and per-group pack write permission for one request."""

    def __init__(self, user: User, db: AsyncSession):
        self.user = user
        self.db = db
        self._team_ids: set[int] | None = None
        self._write: dict[int, bool] = {}

    async def team_ids(self) -> set[int]:
        if self._team_ids is None:
            self._team_ids = await get_user_team_ids_transitive(self.user.id, self.db)
        return self._team_ids

    async def can_view(self, group: DeviceGroup) -> bool:
        team_ids = await self.team_ids()
        return any(t.id in team_ids for t in group.teams)

    async def can_write(self, group: DeviceGroup) -> bool:
        if group.id not in self._write:
            allowed = False
            for team in group.teams:
                if await has_team_pack_permission(team.id, self.user.id, self.db):
                    allowed = True
                    break
            self._write[group.id] = allowed
        return self._write[group.id]

    async def can_view_exclusion(self, exclusion: Exclusion) -> bool:
        for group in exclusion.device_groups:
            if await self.can_view(group):
                return True
        return False

    async def can_edit_exclusion(self, exclusion: Exclusion) -> bool:
        if not exclusion.device_groups:
            return False
        for group in exclusion.device_groups:
            if not await self.can_write(group):
                return False
        return True

    async def require_view(self, exclusion: Exclusion) -> None:
        if not await self.can_view_exclusion(exclusion):
            raise HTTPException(status_code=403, detail="Not authorized")

    async def require_edit(self, exclusion: Exclusion) -> None:
        if not await self.can_edit_exclusion(exclusion):
            raise HTTPException(
                status_code=403,
                detail="Pack write permission on every device group of this exclusion is required",
            )

    async def require_write(self, group: DeviceGroup) -> None:
        if not await self.can_write(group):
            raise HTTPException(status_code=403, detail="No pack write permission")


async def load_exclusion(exclusion_id: int, db: AsyncSession, with_devices: bool = False) -> Exclusion:
    options = list(EXCLUSION_LOAD_OPTIONS)
    if with_devices:
        options.append(selectinload(Exclusion.device_groups).selectinload(DeviceGroup.devices))
    result = await db.execute(select(Exclusion).options(*options).where(Exclusion.id == exclusion_id))
    exclusion = result.scalar_one_or_none()
    if not exclusion:
        raise HTTPException(status_code=404, detail="Exclusion not found")
    return exclusion


async def load_group(group_id: int, db: AsyncSession) -> DeviceGroup:
    result = await db.execute(select(DeviceGroup).options(selectinload(DeviceGroup.teams)).where(DeviceGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


async def group_names(group_ids: Iterable[int | None], db: AsyncSession) -> dict[int, str]:
    ids = {gid for gid in group_ids if gid is not None}
    if not ids:
        return {}
    result = await db.execute(select(DeviceGroup.id, DeviceGroup.name).where(DeviceGroup.id.in_(ids)))
    return dict(result.all())


async def build_responses(
    exclusions: Iterable[Exclusion],
    access: ExclusionAccess,
    context_group_id: int | None = None,
) -> list[ExclusionResponse]:
    exclusions = list(exclusions)
    names = await group_names((e.last_action_group_id for e in exclusions), access.db)
    responses = []
    for e in exclusions:
        visible = [g for g in e.device_groups if await access.can_view(g)]
        visible.sort(key=lambda g: g.name.lower())
        device_group_id = context_group_id
        if device_group_id is None and e.device_groups:
            device_group_id = min(g.id for g in e.device_groups)
        responses.append(
            ExclusionResponse(
                id=e.id,
                device_group_id=device_group_id,
                name=e.name,
                description=e.description,
                jsonata_query=e.jsonata_query,
                created_at=e.created_at,
                alert_id=e.alert_id,
                exclusion_type=e.exclusion_type,
                encrypted=e.encrypted,
                version=e.version,
                groups=[ExclusionGroupRef(id=g.id, name=g.name) for g in visible],
                group_count=len(e.device_groups),
                can_edit=await access.can_edit_exclusion(e),
                last_change=ExclusionLastChange(
                    action=e.last_action,
                    at=e.updated_at or e.created_at,
                    by_user_id=e.updated_by_user_id,
                    by_email=e.updated_by.email if e.updated_by is not None else None,
                    group_id=e.last_action_group_id,
                    group_name=names.get(e.last_action_group_id) if e.last_action_group_id is not None else None,
                ),
            )
        )
    return responses


async def build_response(exclusion: Exclusion, access: ExclusionAccess, context_group_id: int | None = None) -> ExclusionResponse:
    return (await build_responses([exclusion], access, context_group_id))[0]


async def detach_group_exclusions(group_id: int, db: AsyncSession, actor_user_id: int | None) -> None:
    """Prepare a device group for deletion.

    Exclusions linked only to this group are deleted; exclusions shared with other groups lose
    the link and record a 'removed' change. Must be called before the group itself is deleted.
    """
    result = await db.execute(
        select(Exclusion).options(selectinload(Exclusion.device_groups)).where(Exclusion.device_groups.any(DeviceGroup.id == group_id))
    )
    for exclusion in result.scalars().all():
        remaining = [g for g in exclusion.device_groups if g.id != group_id]
        if not remaining:
            await db.delete(exclusion)
        else:
            exclusion.device_groups = remaining
            record_change(exclusion, "removed", actor_user_id, group_id)
    await db.flush()
