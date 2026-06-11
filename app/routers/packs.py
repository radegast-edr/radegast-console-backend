import random
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user, get_session
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.pack import Pack
from app.models.pack_enabled import PackEnabled
from app.models.pack_version import PackVersion
from app.models.team import Team
from app.models.user import User, UserRole
from app.schemas.pack import (
    PackCreate,
    PackEnabledCreate,
    PackEnabledResponse,
    PackResponse,
    PackUpdate,
    PackVersionResponse,
)
from app.services.pack_validation import validate_zip_contents
from app.services.packs import delete_pack_files, get_upload_path, save_upload
from app.services.permissions import (
    get_user_team_ids_transitive,
    is_user_member_of_team_transitive,
)
from app.utils import utc_now

router = APIRouter(prefix="/packs", tags=["packs"])


async def get_latest_version(pack: Pack, db: AsyncSession) -> PackVersion | None:
    """Get the latest version for a pack."""
    result = await db.execute(select(PackVersion).where(PackVersion.pack_id == pack.id).order_by(PackVersion.released.desc()).limit(1))
    return result.scalar_one_or_none()


async def check_pack_write_permission(pack: Pack, user: User, db: AsyncSession) -> bool:
    if not pack.teams:
        if user.role in (UserRole.admin, UserRole.maintainer):
            return True
    if pack.creator_id == user.id:
        return True
    for team in pack.teams:
        if team.permission_pack == "write":
            if await is_user_member_of_team_transitive(team.id, user.id, db):
                return True
    return False


@router.get("/", response_model=list[PackResponse])
async def list_packs(request: Request, db: AsyncSession = Depends(get_db)):
    user = None
    try:
        session = await get_session(request, db)
        user = await get_current_user(request, session, db)
    except HTTPException:
        pass

    result = await db.execute(select(Pack).options(selectinload(Pack.teams)))
    packs = result.scalars().all()
    if not user:
        packs = [p for p in packs if not p.teams]
    else:
        user_team_ids = await get_user_team_ids_transitive(user.id, db)
        packs = [p for p in packs if not p.teams or p.creator_id == user.id or any(t.id in user_team_ids for t in p.teams)]

    # Load latest versions for all packs
    pack_ids = [p.id for p in packs]
    if pack_ids:
        result = await db.execute(
            select(PackVersion).where(PackVersion.pack_id.in_(pack_ids)).order_by(PackVersion.pack_id, PackVersion.released.desc())
        )
        versions = result.scalars().all()
        # Group by pack_id and get the latest
        latest_map = {}
        for v in versions:
            if v.pack_id not in latest_map:
                latest_map[v.pack_id] = v
    else:
        latest_map = {}

    return [
        PackResponse(
            id=p.id,
            pack_id=p.pack_id,
            name=p.name,
            description=p.description,
            creator_id=p.creator_id,
            team_ids=p.team_ids,
            latest=latest_map.get(p.id),
        )
        for p in packs
    ]


@router.post("/", response_model=PackResponse)
async def create_pack(
    data: PackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not data.team_ids:
        if user.role not in (UserRole.maintainer, UserRole.admin):
            raise HTTPException(
                status_code=403,
                detail="Maintainer or admin role required to create public packs",
            )
    else:
        for team_id in data.team_ids:
            res_t = await db.execute(select(Team).where(Team.id == team_id))
            team = res_t.scalar_one_or_none()
            if not team:
                raise HTTPException(status_code=404, detail=f"Team with ID {team_id} not found")
            if user.role not in (UserRole.admin, UserRole.maintainer):
                is_member = await is_user_member_of_team_transitive(team_id, user.id, db)
                if not (is_member and team.permission_pack == "write"):
                    raise HTTPException(
                        status_code=403,
                        detail=f"No pack write permission on team {team_id}",
                    )

    # Validate or generate pack_id
    if not data.pack_id:
        pack_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", data.name.lower()).strip("-")
        if not pack_id:
            pack_id = "pack"
    else:
        pack_id = data.pack_id

    # Validate characters
    if not re.match(r"^[a-zA-Z0-9_-]+$", pack_id):
        raise HTTPException(
            status_code=400,
            detail="pack_id must only contain alphanumeric characters, dashes, and underscores",
        )

    # Check for uniqueness
    existing_id = await db.execute(select(Pack).where(Pack.pack_id == pack_id))
    if existing_id.scalar_one_or_none():
        if data.pack_id:
            raise HTTPException(status_code=400, detail="Pack with this pack_id already exists")
        else:
            pack_id = f"{pack_id}-{random.randint(1000, 9999)}"

    existing = await db.execute(select(Pack).where(Pack.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Pack with this name already exists")

    pack = Pack(
        name=data.name,
        pack_id=pack_id,
        description=data.description,
        creator_id=user.id,
    )
    if data.team_ids:
        res_teams = await db.execute(select(Team).where(Team.id.in_(data.team_ids)))
        pack.teams = res_teams.scalars().all()

    db.add(pack)
    await db.commit()
    await db.refresh(pack)
    result_ref = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack.id))
    pack = result_ref.scalar_one()
    return PackResponse(
        id=pack.id,
        pack_id=pack.pack_id,
        name=pack.name,
        description=pack.description,
        creator_id=pack.creator_id,
        team_ids=pack.team_ids,
        latest=None,
    )


@router.get("/{pack_id}", response_model=PackResponse)
async def get_pack(pack_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = None
    try:
        session = await get_session(request, db)
        user = await get_current_user(request, session, db)
    except HTTPException:
        pass

    result = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    if pack.teams:
        if not user:
            raise HTTPException(status_code=403, detail="Access denied")
        user_team_ids = await get_user_team_ids_transitive(user.id, db)
        if pack.creator_id != user.id and not any(t.id in user_team_ids for t in pack.teams):
            raise HTTPException(status_code=403, detail="Access denied")

    # Get latest version
    latest = await get_latest_version(pack, db)

    return PackResponse(
        id=pack.id,
        pack_id=pack.pack_id,
        name=pack.name,
        description=pack.description,
        creator_id=pack.creator_id,
        team_ids=pack.team_ids,
        latest=latest,
    )


@router.patch("/{pack_id}", response_model=PackResponse)
async def update_pack(
    pack_id: int,
    data: PackUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    if not await check_pack_write_permission(pack, user, db):
        raise HTTPException(status_code=403, detail="No write permission for this pack")

    if data.name is not None:
        if data.name != pack.name:
            existing = await db.execute(select(Pack).where(Pack.name == data.name))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Pack with this name already exists")
        pack.name = data.name

    if data.pack_id is not None:
        if data.pack_id != pack.pack_id:
            if not re.match(r"^[a-zA-Z0-9_-]+$", data.pack_id):
                raise HTTPException(
                    status_code=400,
                    detail="pack_id must only contain alphanumeric characters, dashes, and underscores",
                )
            existing = await db.execute(select(Pack).where(Pack.pack_id == data.pack_id))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Pack with this pack_id already exists")
        pack.pack_id = data.pack_id

    if data.description is not None:
        pack.description = data.description

    if data.team_ids is not None:
        if not data.team_ids:
            if user.role not in (UserRole.admin, UserRole.maintainer):
                raise HTTPException(
                    status_code=403,
                    detail="Maintainer or admin role required to make pack public",
                )
            pack.teams = []
        else:
            for team_id in data.team_ids:
                res_t = await db.execute(select(Team).where(Team.id == team_id))
                team = res_t.scalar_one_or_none()
                if not team:
                    raise HTTPException(status_code=404, detail=f"Team with ID {team_id} not found")
                if user.role not in (UserRole.admin, UserRole.maintainer):
                    is_member = await is_user_member_of_team_transitive(team_id, user.id, db)
                    if not (is_member and team.permission_pack == "write"):
                        raise HTTPException(
                            status_code=403,
                            detail=f"No pack write permission on team {team_id}",
                        )

            res_teams = await db.execute(select(Team).where(Team.id.in_(data.team_ids)))
            pack.teams = res_teams.scalars().all()

    await db.commit()
    await db.refresh(pack)
    result_ref = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack.id))
    pack = result_ref.scalar_one()

    # Get latest version
    latest = await get_latest_version(pack, db)

    return PackResponse(
        id=pack.id,
        pack_id=pack.pack_id,
        name=pack.name,
        description=pack.description,
        creator_id=pack.creator_id,
        team_ids=pack.team_ids,
        latest=latest,
    )


@router.get("/{pack_id}/versions", response_model=list[PackVersionResponse])
async def list_versions(pack_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = None
    try:
        session = await get_session(request, db)
        user = await get_current_user(request, session, db)
    except HTTPException:
        pass

    result_p = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack_id))
    pack = result_p.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    if pack.teams:
        if not user:
            raise HTTPException(status_code=403, detail="Access denied")
        user_team_ids = await get_user_team_ids_transitive(user.id, db)
        if pack.creator_id != user.id and not any(t.id in user_team_ids for t in pack.teams):
            raise HTTPException(status_code=403, detail="Access denied")

    result = await db.execute(select(PackVersion).where(PackVersion.pack_id == pack_id).order_by(PackVersion.released.desc()))
    return result.scalars().all()


@router.post("/{pack_id}/versions", response_model=PackVersionResponse)
async def upload_version(
    pack_id: int,
    version: str,
    file: UploadFile = File(...),
    release_notes: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    if not await check_pack_write_permission(pack, user, db):
        raise HTTPException(status_code=403, detail="No write permission for this pack")

    # Validate semantic versioning format major.minor.patch
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise HTTPException(
            status_code=400,
            detail="Invalid version format. Version must be in major.minor.patch format (e.g., 1.0.0)",
        )
    new_ver = tuple(map(int, version.split(".")))
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    # Check version doesn't already exist and is strictly higher than all existing versions
    existing_versions = await db.execute(select(PackVersion).where(PackVersion.pack_id == pack_id))
    for pv in existing_versions.scalars().all():
        if not re.match(r"^\d+\.\d+\.\d+$", pv.version):
            continue  # skip validation if existing version in database doesn't follow semver (e.g. legacy)
        exist_ver = tuple(map(int, pv.version.split(".")))
        if new_ver <= exist_ver:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded version {version} must be higher than existing version {pv.version}",
            )

    # Read and validate the zip file contents
    content = await file.read()

    # Validate the zip contents
    validation_result = await validate_zip_contents(content)
    if not validation_result["valid"]:
        error_details = validation_result["errors"]
        # Return a properly formatted error response
        raise HTTPException(
            status_code=400,
            detail={"message": "Pack validation failed", "errors": error_details},
        )

    filename = "pack.zip"
    path = get_upload_path(pack_id, version, filename)
    await save_upload(content, path)

    # Save meta from pack.yml if present
    meta = validation_result.get("meta")

    pv = PackVersion(
        pack_id=pack_id,
        version=version,
        zip_path=path,
        release_notes=release_notes,
        meta=meta,
    )
    db.add(pv)
    await db.flush()

    # Auto-update groups using this pack with autoupdate enabled
    result_pe = await db.execute(
        select(PackEnabled)
        .join(PackVersion, PackEnabled.pack_version_id == PackVersion.id)
        .where(PackVersion.pack_id == pack_id, PackEnabled.autoupdate)
    )
    for pe in result_pe.scalars().all():
        pe.pack_version_id = pv.id

    await db.commit()
    await db.refresh(pv)
    return pv


# Enable pack for a device group
@router.post("/groups/{group_id}/enable", response_model=PackEnabledResponse)
async def enable_pack_for_group(
    group_id: int,
    data: PackEnabledCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check user has write permission on at least one team linked to this group
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    has_write = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db) and team.permission_pack == "write":
            has_write = True
            break
    if not has_write:
        raise HTTPException(status_code=403, detail="No pack write permission for this group")

    # Verify pack version exists
    result = await db.execute(select(PackVersion).options(selectinload(PackVersion.pack)).where(PackVersion.id == data.pack_version_id))
    pv = result.scalar_one_or_none()
    if not pv:
        raise HTTPException(status_code=404, detail="Pack version not found")

    pe = PackEnabled(
        device_group_id=group_id,
        pack_version_id=data.pack_version_id,
        autoupdate=data.autoupdate,
    )
    db.add(pe)
    await db.commit()
    await db.refresh(pe)

    return PackEnabledResponse(
        id=pe.id,
        pack_version_id=pe.pack_version_id,
        autoupdate=pe.autoupdate,
        pack_name=pv.pack.name,
        pack_id=pv.pack.pack_id,
        version=pv.version,
    )


@router.get("/groups/{group_id}/enabled", response_model=list[PackEnabledResponse])
async def list_enabled_packs(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check user has at least read permission
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    has_read = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db) and team.permission_pack is not None:
            has_read = True
            break
    if not has_read:
        raise HTTPException(status_code=403, detail="No pack permission for this group")

    result = await db.execute(
        select(PackEnabled)
        .options(selectinload(PackEnabled.pack_version).selectinload(PackVersion.pack))
        .where(PackEnabled.device_group_id == group_id)
    )
    enabled = result.scalars().all()

    return [
        PackEnabledResponse(
            id=pe.id,
            pack_version_id=pe.pack_version_id,
            autoupdate=pe.autoupdate,
            pack_name=pe.pack_version.pack.name,
            pack_id=pe.pack_version.pack.pack_id,
            version=pe.pack_version.version,
        )
        for pe in enabled
    ]


@router.delete("/groups/{group_id}/enabled/{enabled_id}")
async def disable_pack(
    group_id: int,
    enabled_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check write permission
    result = await db.execute(
        select(DeviceGroup).options(selectinload(DeviceGroup.teams).selectinload(Team.users)).where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    has_write = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db) and team.permission_pack == "write":
            has_write = True
            break
    if not has_write:
        raise HTTPException(status_code=403, detail="No pack write permission")

    result = await db.execute(select(PackEnabled).where(PackEnabled.id == enabled_id, PackEnabled.device_group_id == group_id))
    pe = result.scalar_one_or_none()
    if not pe:
        raise HTTPException(status_code=404, detail="Enabled pack not found")

    await db.delete(pe)
    await db.commit()
    return {"message": "Pack disabled for group"}


@router.delete("/{pack_id}")
async def delete_pack(
    pack_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Pack).options(selectinload(Pack.teams)).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    if not await check_pack_write_permission(pack, user, db):
        raise HTTPException(status_code=403, detail="No write permission for this pack")

    delete_pack_files(pack_id)
    await db.delete(pack)
    await db.commit()
    return {"message": "Pack deleted"}


# Device wrapper endpoints
@router.get("/device/available")
async def device_available_packs(
    agent_version: str | None = None,
    rustinel_version: str | None = None,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Device)
        .options(
            selectinload(Device.groups)
            .selectinload(DeviceGroup.packs)
            .selectinload(PackEnabled.pack_version)
            .selectinload(PackVersion.pack)
        )
        .where(Device.id == device.id)
    )
    device = result.scalar_one()
    device.last_seen = utc_now()
    if agent_version is not None:
        device.agent_version = agent_version
    if rustinel_version is not None:
        device.rustinel_version = rustinel_version
    await db.commit()

    packs = []
    for group in device.groups:
        for pe in group.packs:
            packs.append(
                {
                    "enabled_id": pe.id,
                    "pack_id": pe.pack_version.pack.pack_id,
                    "pack_name": pe.pack_version.pack.name,
                    "version": pe.pack_version.version,
                    "pack_version_id": pe.pack_version_id,
                    "autoupdate": pe.autoupdate,
                }
            )

    return packs


@router.get("/download/{version_id}")
async def download_pack_for_user(
    version_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackVersion).options(selectinload(PackVersion.pack).selectinload(Pack.teams)).where(PackVersion.id == version_id)
    )
    pv = result.scalar_one_or_none()
    if not pv:
        raise HTTPException(status_code=404, detail="Pack version not found")

    if pv.pack.teams:
        user_team_ids = await get_user_team_ids_transitive(user.id, db)
        if pv.pack.creator_id != user.id and not any(t.id in user_team_ids for t in pv.pack.teams):
            raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(pv.zip_path, media_type="application/zip")


@router.get("/device/download/{version_id}")
async def download_pack(
    version_id: int,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):

    result = await db.execute(
        select(PackVersion).options(selectinload(PackVersion.pack).selectinload(Pack.teams)).where(PackVersion.id == version_id)
    )
    pv = result.scalar_one_or_none()
    if not pv:
        raise HTTPException(status_code=404, detail="Pack version not found")

    if pv.pack.teams:
        result_d = await db.execute(
            select(Device).options(selectinload(Device.groups).selectinload(DeviceGroup.teams)).where(Device.id == device.id)
        )
        dev = result_d.scalar_one()
        device_team_ids = {t.id for g in dev.groups for t in g.teams}
        pack_team_ids = {t.id for t in pv.pack.teams}
        if not (device_team_ids & pack_team_ids):
            raise HTTPException(status_code=403, detail="Device not authorized to download this pack")

    return FileResponse(pv.zip_path, media_type="application/zip")
