import base64
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from jinja2 import Template

from app.config import settings

install_router = APIRouter(prefix="/device", tags=["device"])

ROOT_DIR = Path(__file__).parent.parent.parent
AGENT_CONFIG_DIR = ROOT_DIR / "agent" / "config"
if not AGENT_CONFIG_DIR.exists():
    AGENT_CONFIG_DIR = Path(__file__).parent.parent / "agent_config"
RELEASES_DIR = Path(settings.releases_dir)


def get_latest_agent_version(os_name: str | None = None, arch_name: str | None = None) -> str | None:
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
                if os_name and arch_name:
                    zip_path = path / os_name / arch_name / "rustinel.zip"
                    if not zip_path.exists():
                        continue
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
    elif os_name == "mac" and arch_name not in {"amd64", "m5"}:
        raise HTTPException(status_code=404, detail="Agent release not found")
    elif os_name not in {"linux", "windows", "mac"}:
        raise HTTPException(status_code=404, detail="Agent release not found")

    if version:
        if not re.match(r"^\d+\.\d+\.\d+(?:r\d+)?$", version):
            raise HTTPException(status_code=404, detail="Agent release not found")
    else:
        version = get_latest_agent_version(os_name=os_name, arch_name=arch_name)
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
    request: Request,
    os_param: str = Query(..., alias="os"),
    rustinel_version: str = Query("latest", alias="rustinel-version"),
    rustinel_autoupdate: bool | None = Query(None, alias="rustinel-autoupdate"),
    agent_autoupdate: bool | None = Query(None, alias="agent-autoupdate"),
    agent_send_severity: bool | None = Query(None, alias="agent-send-severity"),
    agent_send_rule_id: bool | None = Query(None, alias="agent-send-rule-id"),
    agent_healthcheck: bool | None = Query(None, alias="agent-healthcheck"),
):
    os_name = os_param.lower()
    if os_name not in ("linux", "windows", "mac"):
        raise HTTPException(
            status_code=400,
            detail="Only linux, windows and mac OS are supported for automatic installation",
        )

    query = request.query_params
    if (rustinel_version is None or rustinel_version == "latest") and "rustinel_version" in query:
        rustinel_version = query["rustinel_version"]

    def _parse_bool(val: Any) -> bool | None:
        if isinstance(val, bool):
            return val
        if val is None:
            return None
        if isinstance(val, str):
            low = val.strip().lower()
            if low in ("true", "1", "yes", "on", ""):
                return True
            if low in ("false", "0", "no", "off"):
                return False
        return None

    if agent_autoupdate is None:
        for k in ("agent_autoupdate", "agent-autoupdate", "agent autoupdate"):
            if k in query:
                agent_autoupdate = _parse_bool(query[k])
                break

    if agent_send_severity is None:
        for k in ("agent_send_severity", "agent-send-severity", "agent-send_severity", "agent send severity"):
            if k in query:
                agent_send_severity = _parse_bool(query[k])
                break

    if agent_send_rule_id is None:
        for k in ("agent_send_rule_id", "agent-send-rule-id", "agent-send_rule_id", "agent send rule id"):
            if k in query:
                agent_send_rule_id = _parse_bool(query[k])
                break

    if agent_healthcheck is None:
        for k in ("agent_healthcheck", "agent-healthcheck", "agent healthcheck"):
            if k in query:
                agent_healthcheck = _parse_bool(query[k])
                break

    if rustinel_autoupdate is None:
        for k in ("rustinel_autoupdate", "rustinel-autoupdate", "rustinel autoupdate"):
            if k in query:
                rustinel_autoupdate = _parse_bool(query[k])
                break
    # Default to True if not explicitly set
    if rustinel_autoupdate is None:
        rustinel_autoupdate = True

    rustinel_version_query = ""
    if rustinel_version and rustinel_version.lower() != "latest":
        if not re.match(r"^\d+\.\d+\.\d+(?:r\d+)?$", rustinel_version):
            raise HTTPException(status_code=400, detail="Invalid rustinel version format")
        rustinel_version_query = f"&version={rustinel_version}"

    if settings.base_url in ("http://localhost:8000", "http://127.0.0.1:8000"):
        backend_url = str(request.base_url).rstrip("/")
    else:
        backend_url = settings.base_url.rstrip("/")

    if os_name == "linux":
        config_tmpl = AGENT_CONFIG_DIR / "linux" / "config.toml"
        rustinel_service_tmpl = AGENT_CONFIG_DIR / "linux" / "rustinel.service"
        radegast_service_tmpl = AGENT_CONFIG_DIR / "linux" / "radegast-agent.service"
        install_script_tmpl = AGENT_CONFIG_DIR / "linux" / "install.sh"
        rustinel_updater_service_tmpl = AGENT_CONFIG_DIR / "linux" / "rustinel-updater.service"

        if not (
            config_tmpl.exists() and rustinel_service_tmpl.exists() and radegast_service_tmpl.exists() and install_script_tmpl.exists()
        ):
            raise HTTPException(status_code=500, detail="Installation templates missing on server")

        config_content = config_tmpl.read_text()
        rustinel_service_content = rustinel_service_tmpl.read_text()
        radegast_service_content = radegast_service_tmpl.read_text()
        install_script_content = install_script_tmpl.read_text()
        rustinel_updater_service_content = rustinel_updater_service_tmpl.read_text() if rustinel_updater_service_tmpl.exists() else ""

        # Prefill service
        radegast_service_content = radegast_service_content.replace(
            "{{RADEGAST_AGENT_PATH}}",
            "/opt/radegast/home/.local/bin/radegast-edr-agent",
        ).replace("{{RADEGAST_AGENT_BACKEND_URL}}", backend_url)

        extra_linux_env = []
        if agent_send_severity is not None:
            extra_linux_env.append(f"Environment=RADEGAST_AGENT_SEND_SEVERITY={'true' if agent_send_severity else 'false'}")
        if agent_send_rule_id is not None:
            extra_linux_env.append(f"Environment=RADEGAST_AGENT_SEND_RULE_ID={'true' if agent_send_rule_id else 'false'}")
        if agent_healthcheck is not None:
            extra_linux_env.append(f"Environment=RADEGAST_AGENT_HEALTHCHECK={'true' if agent_healthcheck else 'false'}")
        if agent_autoupdate is True:
            extra_linux_env.append("Environment=HOME=/opt/radegast/home")
            extra_linux_env.append("Environment=UV_TOOL_DIR=/opt/radegast/home/.local/share/uv/tools")
            extra_linux_env.append("Environment=UV_TOOL_BIN_DIR=/opt/radegast/home/.local/bin")
            extra_linux_env.append("Environment=UV_CACHE_DIR=/opt/radegast/home/.cache/uv")
            extra_linux_env.append("Environment=RADEGAST_AGENT_AUTOUPDATE=true")
        elif agent_autoupdate is False:
            extra_linux_env.append("Environment=RADEGAST_AGENT_AUTOUPDATE=false")

        if extra_linux_env:
            extra_block = "\n".join(extra_linux_env) + "\n"
            radegast_service_content = radegast_service_content.replace("[Install]", f"{extra_block}\n[Install]")

        # Render install script via Jinja2
        template = Template(install_script_content)
        rendered_script = template.render(
            backend_url=backend_url,
            config_content=config_content,
            rustinel_service_content=rustinel_service_content,
            radegast_service_content=radegast_service_content,
            agent_package=settings.agent_package,
            rustinel_version_query=rustinel_version_query,
            rustinel_autoupdate=rustinel_autoupdate,
            rustinel_updater_service_content=rustinel_updater_service_content,
        )

        return PlainTextResponse(rendered_script, media_type="text/plain")

    elif os_name == "windows":
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
        config_b64 = base64.b64encode(config_content.encode("utf-8")).decode("utf-8")

        extra_windows_env = []
        if agent_send_severity is not None:
            extra_windows_env.append(
                f'\n      <env name="RADEGAST_AGENT_SEND_SEVERITY" value="{"true" if agent_send_severity else "false"}" />'
            )
        if agent_send_rule_id is not None:
            extra_windows_env.append(
                f'\n      <env name="RADEGAST_AGENT_SEND_RULE_ID" value="{"true" if agent_send_rule_id else "false"}" />'
            )
        if agent_healthcheck is not None:
            extra_windows_env.append(
                f'\n      <env name="RADEGAST_AGENT_HEALTHCHECK" value="{"true" if agent_healthcheck else "false"}" />'
            )
        if agent_autoupdate is False:
            extra_windows_env.append('\n      <env name="RADEGAST_AGENT_AUTOUPDATE" value="false" />')
        extra_env_xml = "".join(extra_windows_env)

        # Render install-service.py using Jinja2
        service_template = Template(install_service_content)
        rendered_service = service_template.render(
            backend_url=backend_url,
            config_b64=config_b64,
            agent_package=settings.agent_package,
            rustinel_version_query=rustinel_version_query,
            agent_autoupdate_py=str(agent_autoupdate is True),
            extra_env_xml=extra_env_xml,
            rustinel_autoupdate_updater_py=str(rustinel_autoupdate is True),
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

    else:
        # macOS installation
        config_tmpl = AGENT_CONFIG_DIR / "mac" / "config.toml"
        rustinel_plist_tmpl = AGENT_CONFIG_DIR / "mac" / "io.rustinel.daemon.plist"
        radegast_plist_tmpl = AGENT_CONFIG_DIR / "mac" / "app.radegast.agent.plist"
        install_script_tmpl = AGENT_CONFIG_DIR / "mac" / "install.sh"
        rustinel_updater_plist_tmpl = AGENT_CONFIG_DIR / "mac" / "app.radegast.rustinel-updater.plist"

        if not (config_tmpl.exists() and rustinel_plist_tmpl.exists() and radegast_plist_tmpl.exists() and install_script_tmpl.exists()):
            raise HTTPException(
                status_code=500,
                detail="macOS installation templates missing on server",
            )

        config_content = config_tmpl.read_text()
        rustinel_plist_content = rustinel_plist_tmpl.read_text()
        radegast_plist_content = radegast_plist_tmpl.read_text()
        install_script_content = install_script_tmpl.read_text()
        rustinel_updater_plist_content = rustinel_updater_plist_tmpl.read_text() if rustinel_updater_plist_tmpl.exists() else ""

        # Prefill agent plist
        radegast_plist_content = radegast_plist_content.replace(
            "{{RADEGAST_AGENT_PATH}}",
            "/Library/Radegast/home/.local/bin/radegast-edr-agent",
        ).replace("{{RADEGAST_AGENT_BACKEND_URL}}", backend_url)

        extra_mac_env = []
        if agent_send_severity is not None:
            extra_mac_env.append(
                f"        <key>RADEGAST_AGENT_SEND_SEVERITY</key>\n        <string>{'true' if agent_send_severity else 'false'}</string>"
            )
        if agent_send_rule_id is not None:
            extra_mac_env.append(
                f"        <key>RADEGAST_AGENT_SEND_RULE_ID</key>\n        <string>{'true' if agent_send_rule_id else 'false'}</string>"
            )
        if agent_healthcheck is not None:
            extra_mac_env.append(
                f"        <key>RADEGAST_AGENT_HEALTHCHECK</key>\n        <string>{'true' if agent_healthcheck else 'false'}</string>"
            )
        if agent_autoupdate is True:
            extra_mac_env.append("        <key>HOME</key>\n        <string>/Library/Radegast/home</string>")
            extra_mac_env.append("        <key>UV_TOOL_DIR</key>\n        <string>/Library/Radegast/home/.local/share/uv/tools</string>")
            extra_mac_env.append("        <key>UV_TOOL_BIN_DIR</key>\n        <string>/Library/Radegast/home/.local/bin</string>")
            extra_mac_env.append("        <key>UV_CACHE_DIR</key>\n        <string>/Library/Radegast/home/.cache/uv</string>")
            extra_mac_env.append("        <key>RADEGAST_AGENT_AUTOUPDATE</key>\n        <string>true</string>")
        elif agent_autoupdate is False:
            extra_mac_env.append("        <key>RADEGAST_AGENT_AUTOUPDATE</key>\n        <string>false</string>")

        if extra_mac_env:
            extra_block = "\n".join(extra_mac_env) + "\n"
            target = "        <key>RADEGAST_AGENT_STATE_DIR</key>\n        <string>/Library/Radegast/state/</string>\n"
            if target in radegast_plist_content:
                radegast_plist_content = radegast_plist_content.replace(target, f"{target}{extra_block}")
            else:
                radegast_plist_content = radegast_plist_content.replace(
                    "</dict>\n    <key>WorkingDirectory</key>",
                    f"{extra_block}    </dict>\n    <key>WorkingDirectory</key>",
                )

        # Render install script via Jinja2
        template = Template(install_script_content)
        rendered_script = template.render(
            backend_url=backend_url,
            config_content=config_content,
            rustinel_plist_content=rustinel_plist_content,
            radegast_plist_content=radegast_plist_content,
            agent_package=settings.agent_package,
            rustinel_version_query=rustinel_version_query,
            rustinel_autoupdate=rustinel_autoupdate,
            rustinel_updater_plist_content=rustinel_updater_plist_content,
        )

        return PlainTextResponse(rendered_script, media_type="text/plain")
