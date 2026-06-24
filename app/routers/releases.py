import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import app.routers.install as install_router_module
from app.dependencies import get_current_user
from app.models.user import User, UserRole

router = APIRouter(prefix="/releases", tags=["releases"])

VALID_OS = {"linux", "windows", "mac"}
VALID_ARCH = {"amd64", "arm64", "m5"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:r\d+)?$")


def _releases_dir() -> Path:
    """Always read from the (patchable) module-level constant so tests can swap it."""
    return install_router_module.RELEASES_DIR


def _list_all_releases() -> list[dict]:
    """Scan the releases directory and return a list of release dicts."""
    base = _releases_dir()
    if not base.exists():
        return []
    releases = []
    for version_dir in sorted(base.iterdir()):
        if not version_dir.is_dir() or not SEMVER_RE.match(version_dir.name):
            continue
        version = version_dir.name
        for os_dir in sorted(version_dir.iterdir()):
            if not os_dir.is_dir():
                continue
            os_name = os_dir.name
            for arch_dir in sorted(os_dir.iterdir()):
                if not arch_dir.is_dir():
                    continue
                arch = arch_dir.name
                zip_path = arch_dir / "rustinel.zip"
                if zip_path.exists():
                    stat = zip_path.stat()
                    releases.append(
                        {
                            "version": version,
                            "os": os_name,
                            "arch": arch,
                            "size_bytes": stat.st_size,
                            "uploaded_at": stat.st_mtime,
                        }
                    )
    return releases


@router.get("/")
async def list_releases():
    """List all available rustinel releases from disk."""
    return _list_all_releases()


def _validate_release_params(version: str, os_name: str, arch: str, is_upload: bool = False) -> tuple[str, str, str]:
    err_code = 400 if is_upload else 404

    if not SEMVER_RE.match(version):
        raise HTTPException(status_code=err_code, detail="Version must be semver (e.g. 1.2.3)")

    os_name = os_name.lower()
    arch = arch.lower()

    if os_name not in VALID_OS:
        raise HTTPException(
            status_code=err_code,
            detail=f"OS must be one of: {', '.join(sorted(VALID_OS))}",
        )

    # Validate specific arch allowed for each OS
    if os_name == "linux" and arch not in {"amd64", "arm64"}:
        raise HTTPException(status_code=err_code, detail="Arch must be one of: amd64, arm64")
    elif os_name == "windows" and arch != "amd64":
        raise HTTPException(status_code=err_code, detail="Arch must be one of: amd64")
    elif os_name == "mac" and arch != "m5":
        raise HTTPException(status_code=err_code, detail="Arch must be one of: m5")
    return version, os_name, arch


@router.post("/")
async def upload_release(
    version: str = Form(...),
    os_name: str = Form(..., alias="os"),
    arch: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload a rustinel release zip. Admin only."""
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin role required")

    version, os_name, arch = _validate_release_params(version, os_name, arch, is_upload=True)

    dest_dir = _releases_dir() / version / os_name / arch
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "rustinel.zip"

    if dest.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Release {version}/{os_name}/{arch} already exists. Delete it first.",
        )

    content = await file.read()
    dest.write_bytes(content)

    stat = dest.stat()
    return {
        "version": version,
        "os": os_name,
        "arch": arch,
        "size_bytes": stat.st_size,
        "uploaded_at": stat.st_mtime,
    }


@router.delete("/{version}/{os_name}/{arch}")
async def delete_release(
    version: str,
    os_name: str,
    arch: str,
    user: User = Depends(get_current_user),
):
    """Delete a specific release. Admin only."""
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin role required")

    version, os_name, arch = _validate_release_params(version, os_name, arch)

    zip_path = _releases_dir() / version / os_name / arch / "rustinel.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Release not found")

    # Remove the arch dir; clean up empty parent dirs
    arch_dir = zip_path.parent
    shutil.rmtree(arch_dir)
    # Remove os dir if empty
    os_dir = arch_dir.parent
    if os_dir.exists() and not any(os_dir.iterdir()):
        os_dir.rmdir()
    # Remove version dir if empty
    ver_dir = os_dir.parent
    if ver_dir.exists() and not any(ver_dir.iterdir()):
        ver_dir.rmdir()

    return {"message": f"Release {version}/{os_name}/{arch} deleted"}


@router.get("/{version}/{os_name}/{arch}/download")
async def download_release(
    version: str,
    os_name: str,
    arch: str,
    user: User = Depends(get_current_user),
):
    """Download a specific release zip. Any authenticated user."""
    version, os_name, arch = _validate_release_params(version, os_name, arch)

    zip_path = _releases_dir() / version / os_name / arch / "rustinel.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Release not found")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"rustinel-{version}-{os_name}-{arch}.zip",
    )
