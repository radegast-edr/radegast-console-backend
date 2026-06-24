import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from jinja2 import Template

from app.config import settings

install_router = APIRouter(prefix="/device", tags=["device"])

ROOT_DIR = Path(__file__).parent.parent.parent
AGENT_CONFIG_DIR = ROOT_DIR / "agent" / "config"
if not AGENT_CONFIG_DIR.exists():
    AGENT_CONFIG_DIR = Path(__file__).parent.parent / "agent_config"
RELEASES_DIR = Path(settings.releases_dir)


def get_latest_agent_version() -> str | None:
    releases_dir = RELEASES_DIR
    if not releases_dir.exists():
        return None
    versions = []
    for item in os.listdir(releases_dir):
        path = releases_dir / item
        if path.is_dir():
            match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:r(\d+))?$", item)
            if match:
                major, minor, patch, r = match.groups()
                r_val = int(r) if r is not None else 0
                versions.append(((int(major), int(minor), int(patch), r_val), item))
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

    # Whitelist check for OS and arch combinations
    if os_name == "linux" and arch_name not in {"amd64", "arm64"}:
        raise HTTPException(status_code=404, detail="Agent release not found")
    elif os_name == "windows" and arch_name != "amd64":
        raise HTTPException(status_code=404, detail="Agent release not found")
    elif os_name == "mac" and arch_name != "m5":
        raise HTTPException(status_code=404, detail="Agent release not found")
    elif os_name not in {"linux", "windows", "mac"}:
        raise HTTPException(status_code=404, detail="Agent release not found")

    if version:
        if not re.match(r"^\d+\.\d+\.\d+(?:r\d+)?$", version):
            raise HTTPException(status_code=404, detail="Agent release not found")
    else:
        version = get_latest_agent_version()
        if not version:
            raise HTTPException(status_code=404, detail="No agent releases found")

    zip_path = (RELEASES_DIR / version / os_name / arch_name / "rustinel.zip").resolve()
    if not zip_path.is_relative_to(RELEASES_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not zip_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Agent release not found for version={version}, os={os_name}, arch={arch_name}",
        )

    return FileResponse(zip_path, media_type="application/zip", filename="rustinel.zip")


def build_base64_write_and_decode_block(
    base64_str: str,
    base64_file_env_var: str = "SB",
    base64_file_path: str = "%INSTALL_B64%",
    output_file_env_var: str = "SO",
    output_file_path: str = "%INSTALL_SCRIPT%",
    line_size: int = 2000,
    progress_label: str = "Decoding script",
    delete_base64_file: bool = False,
) -> str:
    chunks = [base64_str[i : i + line_size] for i in range(0, len(base64_str), line_size)]
    block = ""
    block += f"set {base64_file_env_var}={base64_file_path}\r\n"
    block += f"set {output_file_env_var}={output_file_path}\r\n"
    if delete_base64_file:
        block += f'if exist "%{base64_file_env_var}%" del "%{base64_file_env_var}%"\r\n'

    for i, chunk in enumerate(chunks):
        op = ">" if i == 0 else ">>"
        current = i + 1
        total = len(chunks)
        percent = int((current / total) * 100)
        block += f'(echo {chunk}){op}"%{base64_file_env_var}%"\r\n'
        block += f"echo {progress_label}: {current}/{total} ({percent}%)\r\n"

    block += f'%POWERSHELL_BIN% -Command "$b = Get-Content -Path $env:{base64_file_env_var} -Raw; $x = [System.Convert]::FromBase64String($b); [System.IO.File]::WriteAllBytes($env:{output_file_env_var}, $x)"\r\n'
    return block


@install_router.get("/install")
async def get_install_script(
    os_param: str = Query(..., alias="os"),
):
    os_name = os_param.lower()
    if os_name not in ("linux", "windows"):
        raise HTTPException(
            status_code=400,
            detail="Only linux and windows OS are supported for automatic installation",
        )

    backend_url = settings.base_url.rstrip("/")

    if os_name == "linux":
        config_tmpl = AGENT_CONFIG_DIR / "linux" / "config.toml"
        rustinel_service_tmpl = AGENT_CONFIG_DIR / "linux" / "rustinel.service"
        radegast_service_tmpl = AGENT_CONFIG_DIR / "linux" / "radegast-agent.service"
        install_script_tmpl = AGENT_CONFIG_DIR / "linux" / "install.sh"

        if not (
            config_tmpl.exists() and rustinel_service_tmpl.exists() and radegast_service_tmpl.exists() and install_script_tmpl.exists()
        ):
            raise HTTPException(status_code=500, detail="Installation templates missing on server")

        config_content = config_tmpl.read_text()
        rustinel_service_content = rustinel_service_tmpl.read_text()
        radegast_service_content = radegast_service_tmpl.read_text()
        install_script_content = install_script_tmpl.read_text()

        # Prefill service
        radegast_service_content = radegast_service_content.replace(
            "{{RADEGAST_AGENT_PATH}}",
            "/opt/radegast/home/.local/bin/radegast-edr-agent",
        ).replace("{{RADEGAST_AGENT_BACKEND_URL}}", backend_url)

        # Render install script via Jinja2
        template = Template(install_script_content)
        rendered_script = template.render(
            backend_url=backend_url,
            config_content=config_content,
            rustinel_service_content=rustinel_service_content,
            radegast_service_content=radegast_service_content,
        )

        return PlainTextResponse(rendered_script, media_type="text/plain")

    else:
        config_tmpl = AGENT_CONFIG_DIR / "windows" / "config.toml"
        install_service_tmpl = AGENT_CONFIG_DIR / "windows" / "install-service.py"
        install_bat_tmpl = AGENT_CONFIG_DIR / "windows" / "install.bat"

        if not (config_tmpl.exists() and install_service_tmpl.exists() and install_bat_tmpl.exists()):
            raise HTTPException(
                status_code=500,
                detail="Windows installation templates missing on server",
            )

        config_content = config_tmpl.read_text(encoding="utf-8")
        install_service_content = install_service_tmpl.read_text(encoding="utf-8")
        install_bat_content = install_bat_tmpl.read_text(encoding="utf-8")

        # Encode config to base64 to put it in install-service.py
        import base64

        config_b64 = base64.b64encode(config_content.encode("utf-8")).decode("utf-8")

        # Render install-service.py using Jinja2
        service_template = Template(install_service_content)
        rendered_service = service_template.render(
            backend_url=backend_url,
            config_b64=config_b64,
        )

        # Base64 encode the entire install-service.py
        service_b64 = base64.b64encode(rendered_service.encode("utf-8")).decode("utf-8")

        # Build the write and decode block for the batch file
        block = build_base64_write_and_decode_block(
            base64_str=service_b64,
            base64_file_env_var="SB",
            base64_file_path="%INSTALL_B64%",
            output_file_env_var="SO",
            output_file_path="%INSTALL_SCRIPT%",
            line_size=7000,
            progress_label="Decoding script",
            delete_base64_file=False,
        )

        # Render install.bat using Jinja2 template
        bat_template = Template(install_bat_content)
        rendered_bat = bat_template.render(install_service_block=block.strip())

        return PlainTextResponse(rendered_bat, media_type="text/plain")
