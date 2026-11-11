from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.exclusion import Exclusion
from app.models.log import Log
from app.models.team import Team
from app.models.user import User
from app.schemas.exclusion import (
    ExclusionCreate,
    ExclusionCreateMulti,
    ExclusionDeleteResponse,
    ExclusionGroupChange,
    ExclusionRecipientsResponse,
    ExclusionResponse,
    ExclusionUpdate,
)
from app.services.exclusions import (
    EXCLUSION_LOAD_OPTIONS,
    ExclusionAccess,
    build_response,
    build_responses,
    ensure_alert_in_groups,
    ensure_ciphertext,
    ensure_groups_support_e2ee,
    ensure_soft_allowed,
    ensure_version,
    load_exclusion,
    load_group,
    record_change,
)
from app.services.permissions import get_user_team_ids_transitive

router = APIRouter(prefix="/exclusions", tags=["exclusions"])


async def _commit(db: AsyncSession) -> None:
    """Commit, translating optimistic-locking conflicts into HTTP 409."""
    try:
        await db.commit()
    except StaleDataError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail="The exclusion was changed by someone else. Reload it and try again.") from e


async def _load_groups(group_ids: list[int], db: AsyncSession) -> list[DeviceGroup]:
    unique_ids = list(dict.fromkeys(group_ids))
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams), selectinload(DeviceGroup.devices))
        .where(DeviceGroup.id.in_(unique_ids))
    )
    groups = {g.id: g for g in result.scalars().all()}
    missing = [gid for gid in unique_ids if gid not in groups]
    if missing:
        raise HTTPException(status_code=404, detail="Group not found")
    return [groups[gid] for gid in unique_ids]


async def _create(
    data: ExclusionCreateMulti,
    groups: list[DeviceGroup],
    access: ExclusionAccess,
    user: User,
    db: AsyncSession,
    context_group_id: int | None = None,
) -> ExclusionResponse:
    for group in groups:
        await access.require_write(group)
    ensure_soft_allowed(data.exclusion_type, user)
    ensure_ciphertext(data.encrypted, data.name, data.jsonata_query, data.description)
    if data.encrypted:
        await ensure_groups_support_e2ee(groups, db)
    await ensure_alert_in_groups(data.alert_id, [g.id for g in groups], db)

    exclusion = Exclusion(
        name=data.name,
        description=data.description,
        jsonata_query=data.jsonata_query,
        alert_id=data.alert_id,
        exclusion_type=data.exclusion_type,
        encrypted=data.encrypted,
        device_groups=groups,
    )
    record_change(exclusion, "created", user.id)
    db.add(exclusion)
    await db.flush()

    if data.alert_id:
        log = await db.get(Log, data.alert_id)
        if log:
            log.excluded_by = exclusion.id

    await _commit(db)
    return await build_response(await load_exclusion(exclusion.id, db), access, context_group_id)


@router.get("/", response_model=list[ExclusionResponse])
async def list_all_exclusions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all exclusions applied to at least one device group visible to the current user."""
    team_ids = await get_user_team_ids_transitive(user.id, db)
    if not team_ids:
        return []
    result = await db.execute(
        select(Exclusion)
        .options(*EXCLUSION_LOAD_OPTIONS)
        .where(Exclusion.device_groups.any(DeviceGroup.teams.any(Team.id.in_(list(team_ids)))))
        .order_by(Exclusion.id)
    )
    return await build_responses(result.scalars().all(), ExclusionAccess(user, db))


@router.post("/", response_model=ExclusionResponse)
async def create_exclusion(
    data: ExclusionCreateMulti,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an exclusion applied to one or more device groups."""
    groups = await _load_groups(data.group_ids, db)
    return await _create(data, groups, ExclusionAccess(user, db), user, db)


@router.get("/groups/{group_id}", response_model=list[ExclusionResponse])
async def list_group_exclusions(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all exclusions applied to a specific device group."""
    group = await load_group(group_id, db)
    access = ExclusionAccess(user, db)
    if not await access.can_view(group):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(Exclusion)
        .options(*EXCLUSION_LOAD_OPTIONS)
        .where(Exclusion.device_groups.any(DeviceGroup.id == group_id))
        .order_by(Exclusion.id)
    )
    return await build_responses(result.scalars().all(), access, context_group_id=group_id)


@router.post("/groups/{group_id}", response_model=ExclusionResponse)
async def create_group_exclusion(
    group_id: int,
    data: ExclusionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an exclusion applied to a single device group (backwards compatible endpoint)."""
    groups = await _load_groups([group_id], db)
    multi = ExclusionCreateMulti(**data.model_dump(), group_ids=[group_id])
    return await _create(multi, groups, ExclusionAccess(user, db), user, db, context_group_id=group_id)


# Device-facing endpoint for downloading exclusions
@router.get("/device")
async def get_device_exclusions(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """
    Device endpoint: Returns all exclusions (JSONata queries) for the groups this device belongs to.
    An exclusion shared by several of the device's groups is returned once per group.
    The agent will use these to filter out false positives from alerts.
    """
    result = await db.execute(
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.exclusions))
        .where(DeviceGroup.devices.any(Device.id == device.id))  # type: ignore
        .order_by(DeviceGroup.id)
    )
    groups = result.scalars().all()

    exclusions = [
        {
            "id": e.id,
            "name": e.name,
            "jsonata_query": e.jsonata_query,
            "device_group_id": g.id,
            "exclusion_type": e.exclusion_type,
            "encrypted": e.encrypted,
        }
        for g in groups
        for e in sorted(g.exclusions, key=lambda x: x.id)
    ]
    group_keys = {
        g.id: {
            "public_key": g.public_key,
            "private_key": g.private_key,
        }
        for g in groups
    }
    return JSONResponse(content={"exclusions": exclusions, "group_keys": group_keys})


@router.get("/{exclusion_id}", response_model=ExclusionResponse)
async def get_exclusion(
    exclusion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific exclusion by ID."""
    exclusion = await load_exclusion(exclusion_id, db)
    access = ExclusionAccess(user, db)
    await access.require_view(exclusion)
    return await build_response(exclusion, access)


@router.get("/{exclusion_id}/recipients", response_model=ExclusionRecipientsResponse)
async def get_exclusion_recipients(
    exclusion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Public keys of all device groups the exclusion is applied to (the AGE recipients of its content)."""
    exclusion = await load_exclusion(exclusion_id, db)
    access = ExclusionAccess(user, db)
    await access.require_view(exclusion)
    keys = sorted({g.public_key for g in exclusion.device_groups if g.public_key})
    return {"version": exclusion.version, "public_keys": keys}


@router.put("/{exclusion_id}", response_model=ExclusionResponse)
async def update_exclusion(
    exclusion_id: int,
    data: ExclusionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an exclusion in every device group it is applied to."""
    exclusion = await load_exclusion(exclusion_id, db, with_devices=True)
    access = ExclusionAccess(user, db)
    await access.require_edit(exclusion)
    ensure_version(exclusion, data.version)

    exclusion_type = data.exclusion_type or exclusion.exclusion_type
    if exclusion_type != exclusion.exclusion_type:
        ensure_soft_allowed(exclusion_type, user)
    encrypted = exclusion.encrypted if data.encrypted is None else data.encrypted
    if exclusion.encrypted and not encrypted:
        raise HTTPException(status_code=400, detail="An encrypted exclusion cannot be changed to plaintext")
    ensure_ciphertext(encrypted, data.name, data.jsonata_query, data.description)
    if encrypted and not exclusion.encrypted:
        await ensure_groups_support_e2ee(exclusion.device_groups, db)

    exclusion.name = data.name
    exclusion.jsonata_query = data.jsonata_query
    exclusion.description = data.description
    exclusion.exclusion_type = exclusion_type
    exclusion.encrypted = encrypted
    record_change(exclusion, "updated", user.id)
    await _commit(db)
    return await build_response(await load_exclusion(exclusion_id, db), access)


@router.put("/{exclusion_id}/groups/{group_id}", response_model=ExclusionResponse)
async def apply_exclusion_to_group(
    exclusion_id: int,
    group_id: int,
    data: ExclusionGroupChange,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Apply an existing exclusion to another device group."""
    exclusion = await load_exclusion(exclusion_id, db)
    access = ExclusionAccess(user, db)
    await access.require_view(exclusion)
    group = (await _load_groups([group_id], db))[0]
    await access.require_write(group)
    ensure_version(exclusion, data.version)
    if any(g.id == group_id for g in exclusion.device_groups):
        raise HTTPException(status_code=400, detail="The exclusion is already applied to this group")

    if exclusion.encrypted:
        await ensure_groups_support_e2ee([group], db)
        ensure_ciphertext(True, data.name, data.jsonata_query, data.description)
        exclusion.name = data.name  # type: ignore[assignment]
        exclusion.jsonata_query = data.jsonata_query  # type: ignore[assignment]
        exclusion.description = data.description

    exclusion.device_groups.append(group)
    record_change(exclusion, "applied", user.id, group_id)
    await _commit(db)
    return await build_response(await load_exclusion(exclusion_id, db), access)


@router.post("/{exclusion_id}/groups/{group_id}/remove", response_model=ExclusionDeleteResponse)
async def remove_exclusion_from_group(
    exclusion_id: int,
    group_id: int,
    data: ExclusionGroupChange,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an exclusion from a device group. Removing it from its last group deletes it."""
    exclusion = await load_exclusion(exclusion_id, db)
    access = ExclusionAccess(user, db)
    group = next((g for g in exclusion.device_groups if g.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="The exclusion is not applied to this group")
    await access.require_write(group)
    ensure_version(exclusion, data.version)

    remaining = [g for g in exclusion.device_groups if g.id != group_id]
    if not remaining:
        await db.delete(exclusion)
        await _commit(db)
        return {"message": "Exclusion removed from its last group and deleted", "deleted": True}

    if exclusion.encrypted:
        ensure_ciphertext(True, data.name, data.jsonata_query, data.description)
        exclusion.name = data.name  # type: ignore[assignment]
        exclusion.jsonata_query = data.jsonata_query  # type: ignore[assignment]
        exclusion.description = data.description

    exclusion.device_groups = remaining
    record_change(exclusion, "removed", user.id, group_id)
    await _commit(db)
    return {"message": "Exclusion removed from group", "deleted": False}


@router.delete("/{exclusion_id}", response_model=ExclusionDeleteResponse)
async def delete_exclusion(
    exclusion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an exclusion from every device group it is applied to."""
    exclusion = await load_exclusion(exclusion_id, db)
    access = ExclusionAccess(user, db)
    await access.require_edit(exclusion)
    await db.delete(exclusion)
    await _commit(db)
    return {"message": "Exclusion deleted", "deleted": True}
