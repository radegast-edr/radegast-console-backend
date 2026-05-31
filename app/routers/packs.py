from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_device, get_current_user
from app.models.device import Device
from app.utils import utc_now
from app.models.device_group import DeviceGroup
from app.models.pack import Pack
from app.models.pack_enabled import PackEnabled
from app.models.pack_version import PackVersion
from app.models.team import Team
from app.models.user import User, UserRole
from app.schemas.pack import (
    PackCreate,
    PackUpdate,
    PackEnabledCreate,
    PackEnabledResponse,
    PackResponse,
    PackVersionResponse,
)
from app.services.packs import get_upload_path, save_upload, delete_pack_files

router = APIRouter(prefix="/packs", tags=["packs"])


@router.get("/", response_model=list[PackResponse])
async def list_packs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pack))
    return result.scalars().all()


@router.post("/", response_model=PackResponse)
async def create_pack(
    data: PackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.maintainer, UserRole.admin):
        raise HTTPException(status_code=403, detail="Maintainer or admin role required")

    existing = await db.execute(select(Pack).where(Pack.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Pack with this name already exists")

    pack = Pack(name=data.name, description=data.description)
    db.add(pack)
    await db.commit()
    await db.refresh(pack)
    return pack


@router.get("/{pack_id}", response_model=PackResponse)
async def get_pack(pack_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pack).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack


@router.patch("/{pack_id}", response_model=PackResponse)
async def update_pack(
    pack_id: int,
    data: PackUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.maintainer, UserRole.admin):
        raise HTTPException(status_code=403, detail="Maintainer or admin role required")

    result = await db.execute(select(Pack).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    if data.name is not None:
        if data.name != pack.name:
            existing = await db.execute(select(Pack).where(Pack.name == data.name))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Pack with this name already exists")
        pack.name = data.name

    if data.description is not None:
        pack.description = data.description

    await db.commit()
    await db.refresh(pack)
    return pack


@router.get("/{pack_id}/versions", response_model=list[PackVersionResponse])
async def list_versions(pack_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PackVersion).where(PackVersion.pack_id == pack_id).order_by(PackVersion.released.desc())
    )
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
    import re
    if user.role not in (UserRole.maintainer, UserRole.admin):
        raise HTTPException(status_code=403, detail="Maintainer or admin role required")

    # Validate semantic versioning format major.minor.patch
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise HTTPException(
            status_code=400,
            detail="Invalid version format. Version must be in major.minor.patch format (e.g., 1.0.0)"
        )
    new_ver = tuple(map(int, version.split(".")))

    result = await db.execute(select(Pack).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    # Check version doesn't already exist and is strictly higher than all existing versions
    existing_versions = await db.execute(
        select(PackVersion).where(PackVersion.pack_id == pack_id)
    )
    for pv in existing_versions.scalars().all():
        if not re.match(r"^\d+\.\d+\.\d+$", pv.version):
            continue  # skip validation if existing version in database doesn't follow semver (e.g. legacy)
        exist_ver = tuple(map(int, pv.version.split(".")))
        if new_ver <= exist_ver:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded version {version} must be higher than existing version {pv.version}"
            )

    content = await file.read()
    filename = "pack.zip"
    path = get_upload_path(pack_id, version, filename)
    await save_upload(content, path)

    pv = PackVersion(pack_id=pack_id, version=version, zip_path=path, release_notes=release_notes)
    db.add(pv)
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
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    from app.services.permissions import is_user_member_of_team_transitive
    has_write = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db) and team.permission_pack == "write":
            has_write = True
            break
    if not has_write:
        raise HTTPException(status_code=403, detail="No pack write permission for this group")

    # Verify pack version exists
    result = await db.execute(
        select(PackVersion).options(selectinload(PackVersion.pack)).where(PackVersion.id == data.pack_version_id)
    )
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
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    from app.services.permissions import is_user_member_of_team_transitive
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
        select(DeviceGroup)
        .options(selectinload(DeviceGroup.teams).selectinload(Team.users))
        .where(DeviceGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Device group not found")

    from app.services.permissions import is_user_member_of_team_transitive
    has_write = False
    for team in group.teams:
        if await is_user_member_of_team_transitive(team.id, user.id, db) and team.permission_pack == "write":
            has_write = True
            break
    if not has_write:
        raise HTTPException(status_code=403, detail="No pack write permission")

    result = await db.execute(
        select(PackEnabled).where(PackEnabled.id == enabled_id, PackEnabled.device_group_id == group_id)
    )
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
    if user.role not in (UserRole.maintainer, UserRole.admin):
        raise HTTPException(status_code=403, detail="Maintainer or admin role required")

    result = await db.execute(select(Pack).where(Pack.id == pack_id))
    pack = result.scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    delete_pack_files(pack_id)
    await db.delete(pack)
    await db.commit()
    return {"message": "Pack deleted"}


# Device wrapper endpoints
@router.get("/device/available")
async def device_available_packs(
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
    await db.commit()

    packs = []
    for group in device.groups:
        for pe in group.packs:
            packs.append({
                "enabled_id": pe.id,
                "pack_name": pe.pack_version.pack.name,
                "version": pe.pack_version.version,
                "pack_version_id": pe.pack_version_id,
                "autoupdate": pe.autoupdate,
            })

    return packs


@router.get("/download/{version_id}")
async def download_pack_for_user(
    version_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import FileResponse

    result = await db.execute(
        select(PackVersion).where(PackVersion.id == version_id)
    )
    pv = result.scalar_one_or_none()
    if not pv:
        raise HTTPException(status_code=404, detail="Pack version not found")

    return FileResponse(pv.zip_path, media_type="application/zip")


@router.get("/device/download/{version_id}")
async def download_pack(
    version_id: int,
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import FileResponse

    result = await db.execute(
        select(PackVersion).where(PackVersion.id == version_id)
    )
    pv = result.scalar_one_or_none()
    if not pv:
        raise HTTPException(status_code=404, detail="Pack version not found")

    return FileResponse(pv.zip_path, media_type="application/zip")
