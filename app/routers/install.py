from pathlib import Path
import re
import os

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from jinja2 import Template

from app.config import settings

install_router = APIRouter(prefix="/device", tags=["device"])

ROOT_DIR = Path(__file__).parent.parent.parent
AGENT_DIR = ROOT_DIR / "agent"
RELEASES_DIR = Path(settings.releases_dir)


def get_latest_agent_version() -> str | None:
    releases_dir = RELEASES_DIR
    if not releases_dir.exists():
        return None
    versions = []
    for item in os.listdir(releases_dir):
        path = releases_dir / item
        if path.is_dir():
            match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", item)
            if match:
                versions.append((tuple(map(int, match.groups())), item))
    if not versions:
        return None
    versions.sort()
    return versions[-1][1]


@install_router.get("/agent/download")
@install_router.get("/rustinel/download")
async def download_agent(
    os_param: str = Query(..., alias="os"),
    arch_param: str = Query(..., alias="arch"),
    version: str | None = None,
):
    os_name = os_param.lower()
    arch_name = arch_param.lower()
    if arch_name in ("x86_64", "amd64"):
        arch_name = "amd64"
    elif arch_name in ("aarch64", "arm64"):
        arch_name = "arm64"

    if not version:
        version = get_latest_agent_version()
        if not version:
            raise HTTPException(status_code=404, detail="No agent releases found")

    zip_path = RELEASES_DIR / version / os_name / arch_name / "rustinel.zip"
    if not zip_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Agent release not found for version={version}, os={os_name}, arch={arch_name}"
        )

    return FileResponse(zip_path, media_type="application/zip", filename="rustinel.zip")


@install_router.get("/install")
async def get_install_script(
    os_param: str = Query(..., alias="os"),
):
    if os_param.lower() != "linux":
        raise HTTPException(status_code=400, detail="Only linux OS is supported for automatic installation")

    config_tmpl = AGENT_DIR / "config" / "linux" / "config.toml"
    rustinel_service_tmpl = AGENT_DIR / "config" / "linux" / "rustinel.service"
    radegast_service_tmpl = AGENT_DIR / "config" / "linux" / "radegast-agent.service"
    install_script_tmpl = AGENT_DIR / "config" / "linux" / "install.sh"

    if not (
        config_tmpl.exists()
        and rustinel_service_tmpl.exists()
        and radegast_service_tmpl.exists()
        and install_script_tmpl.exists()
    ):
        raise HTTPException(status_code=500, detail="Installation templates missing on server")

    config_content = config_tmpl.read_text()
    rustinel_service_content = rustinel_service_tmpl.read_text()
    radegast_service_content = radegast_service_tmpl.read_text()
    install_script_content = install_script_tmpl.read_text()

    backend_url = settings.base_url.rstrip("/")

    # Prefill service
    radegast_service_content = radegast_service_content.replace(
        "<fix this to the actual absolute path AI>radegast-agent",
        "/opt/radegast/home/.local/bin/radegast-agent"
    ).replace(
        "{{RADEGAST_AGENT_BACKEND_URL}}",
        backend_url
    )

    # Render install script via Jinja2
    template = Template(install_script_content)
    rendered_script = template.render(
        backend_url=backend_url,
        config_content=config_content,
        rustinel_service_content=rustinel_service_content,
        radegast_service_content=radegast_service_content,
    )

    return PlainTextResponse(rendered_script, media_type="text/plain")
