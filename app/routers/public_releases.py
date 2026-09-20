"""Public (unauthenticated) release listing and download endpoints.

These endpoints allow the radegast-rustinel-updater service to discover
and download release binaries without requiring device enrollment or
user authentication.
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import app.routers.install as install_router_module

router = APIRouter(prefix="/public/releases", tags=["public-releases"])

VALID_OS = {"linux", "windows", "mac"}
VALID_ARCH = {"amd64", "arm64", "m5"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:r\d+)?$")


def _releases_dir() -> Path:
    return install_router_module.RELEASES_DIR


@router.get("/")
async def list_public_releases():
    """List all available rustinel releases. No authentication required."""
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
                        }
                    )
    return releases


@router.get("/{version}/{os_name}/{arch}/download")
async def download_public_release(version: str, os_name: str, arch: str):
    """Download a specific release zip. No authentication required."""
    if not SEMVER_RE.match(version):
        raise HTTPException(status_code=404, detail="Invalid version format")
    os_name = os_name.lower()
    arch = arch.lower()
    if os_name not in VALID_OS:
        raise HTTPException(status_code=404, detail=f"OS must be one of: {', '.join(sorted(VALID_OS))}")
    if arch not in VALID_ARCH:
        raise HTTPException(status_code=404, detail=f"Arch must be one of: {', '.join(sorted(VALID_ARCH))}")

    zip_path = _releases_dir() / version / os_name / arch / "rustinel.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Release not found")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"rustinel-{version}-{os_name}-{arch}.zip",
    )
