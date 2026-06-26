import base64
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path


def run_cmd(cmd, check=True, show_output=False):
    """Helper to run shell commands. Shows output if explicitly requested."""
    stdout_dest = None if show_output else subprocess.DEVNULL
    stderr_dest = None if show_output else subprocess.DEVNULL
    try:
        subprocess.run(cmd, check=check, stdout=stdout_dest, stderr=stderr_dest)
    except subprocess.CalledProcessError as e:
        print(f"WARNING/ERROR executing {' '.join(cmd)}: {e}")


def main():
    print("=== Starting Radegast EDR Agent & Rustinel Windows Installation ===")

    # Check for administrative privileges and elevate if needed
    try:
        import ctypes
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        is_admin = False

    if not is_admin:
        print("Requesting administrative privileges...")
        try:
            params = " ".join([f'"{arg}"' for arg in sys.argv])
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{__file__}" {params}', None, 1)
            if ret > 32:
                sys.exit(0)
        except Exception as e:
            print(f"ERROR: Failed to elevate privileges: {e}", file=sys.stderr)
        sys.exit(1)

    # 0. Check RADEGAST_TOKEN environment variable
    token = os.environ.get("RADEGAST_TOKEN")
    if not token:
        print("ERROR: RADEGAST_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Base directory setup
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    radegast_dir = Path(program_files) / "Radegast"
    tools_dir = radegast_dir / ".tools"

    # New Agent Layout Subdirectories
    agent_dir = radegast_dir / "agent"
    agent_home_dir = agent_dir / "home"
    agent_service_dir = agent_dir / "service"
    rules_dir = agent_dir / "rules"
    ioc_dir = rules_dir / "ioc"
    logs_dir = agent_dir / "logs"
    state_dir = agent_dir / "state"
    cache_dir = agent_dir / ".cache"

    # New Rustinel Layout Subdirectories
    rustinel_dir = radegast_dir / "rustinel"
    rustinel_core_dir = rustinel_dir / "rustinel"
    rustinel_service_dir = rustinel_dir / "service"

    # Executable Target Paths
    python_exe_path = Path(sys.executable)
    python_dir = python_exe_path.parent
    rustinel_service_exe = rustinel_service_dir / "radegast-rustinel-service.exe"
    agent_service_exe = agent_service_dir / "radegast-agent-service.exe"

    # 1. Pre-Installation Cleanup (Unlock Files)
    print("Checking for existing services to stop and unlock files...")
    if rustinel_service_exe.exists():
        run_cmd([str(rustinel_service_exe), "stop"], check=False)
        run_cmd([str(rustinel_service_exe), "uninstall"], check=False)
    else:
        run_cmd(["net", "stop", "RadegastRustinel"], check=False)

    if agent_service_exe.exists():
        run_cmd([str(agent_service_exe), "stop"], check=False)
        run_cmd([str(agent_service_exe), "uninstall"], check=False)
    else:
        run_cmd(["net", "stop", "RadegastAgent"], check=False)

    run_cmd(["taskkill", "/f", "/im", "rustinel.exe"], check=False)
    run_cmd(["taskkill", "/f", "/im", "radegast-agent.exe"], check=False)
    time.sleep(2)

    # 2. Setup Directories
    print(f"Creating specialized application directory trees under {radegast_dir}...")
    dirs_to_create = [
        radegast_dir, tools_dir,
        agent_dir, agent_service_dir, rules_dir, ioc_dir, logs_dir, state_dir, cache_dir, agent_home_dir,
        rustinel_dir, rustinel_core_dir, rustinel_service_dir
    ]
    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)

    for filename in ["hashes.txt", "ips.txt", "domains.txt", "paths_regex.txt"]:
        file_path = ioc_dir / filename
        if not file_path.exists():
            file_path.write_text("", encoding="utf-8")

    # 3. Get architecture and download rustinel
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "amd64"
        winsw_url = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
        winsw_url = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-arm64.exe"
    else:
        print(f"ERROR: Unsupported architecture: {machine}", file=sys.stderr)
        sys.exit(1)

    backend_url = "{{ backend_url }}"
    download_url = f"{backend_url}/api/v1/device/agent/download?os=windows&arch={arch}"
    zip_path = rustinel_core_dir / "rustinel.zip"

    print("Downloading rustinel...")
    success = False
    last_error = None
    urls_to_try = [
        download_url,
        f"https://console-api.radegast.app/api/v1/device/agent/download?os=windows&arch={arch}"
    ]
    for url in urls_to_try:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})  # noqa: S310
            import ssl
            ssl_context = ssl._create_unverified_context() if hasattr(ssl, '_create_unverified_context') else None  # noqa: S323
            with urllib.request.urlopen(req, context=ssl_context) as response, open(zip_path, 'wb') as out_file:  # noqa: S310
                out_file.write(response.read())

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(rustinel_core_dir)
            if zip_path.exists():
                zip_path.unlink()
            success = True
            break
        except Exception as e:
            last_error = e
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception:  # noqa: S110
                    pass
            print(f"INFO: Failed to download/extract from {url}: {e}", file=sys.stderr)

    if not success:
        print(f"ERROR: Failed to download/extract rustinel after trying all locations. Last error: {last_error}", file=sys.stderr)
        sys.exit(1)

    # 4. Write configuration file config.toml
    config_b64 = "{{ config_b64 }}"
    config_content = base64.b64decode(config_b64.encode("utf-8")).decode("utf-8")
    (agent_dir / "config.toml").write_text(config_content, encoding="utf-8")

    # 5. Install radegast-agent-python as a tool
    uv_exe = python_dir / "Scripts" / "uv.exe"
    agent_pyproject = agent_home_dir / "pyproject.toml"
    if agent_pyproject.exists():
        agent_pyproject.unlink()

    # Delete existing virtual environment to clean up old layout
    agent_venv = agent_home_dir / ".venv"
    if agent_venv.exists():
        print("Removing legacy agent virtual environment...")
        shutil.rmtree(agent_venv, ignore_errors=True)

    agent_tools_dir = agent_dir / ".tools"
    if agent_tools_dir.exists():
        print("Removing existing agent tool environment to force upgrade...")
        shutil.rmtree(agent_tools_dir, ignore_errors=True)

    tool_bin_dir = agent_home_dir / ".local" / "bin"
    tool_bin_dir.mkdir(parents=True, exist_ok=True)

    print("Installing/upgrading {{ agent_package }} as a tool...")
    env = os.environ.copy()
    env["UV_TOOL_DIR"] = str(agent_tools_dir)
    env["UV_TOOL_BIN_DIR"] = str(tool_bin_dir)
    env["UV_CACHE_DIR"] = str(cache_dir)
    env["UV_PYTHON"] = str(python_exe_path)

    subprocess.run(
        [str(uv_exe), "tool", "install", "--upgrade", "--force", "{{ agent_package }}"],
        check=True,
        env=env,
        cwd=agent_home_dir
    )
    agent_exe = tool_bin_dir / "radegast-edr-agent.exe"

    # 6. Download WinSW and Setup Service XMLs
    print("Downloading WinSW Wrapper...")
    winsw_bin = tools_dir / "winsw.exe"
    try:
        req = urllib.request.Request(winsw_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ssl_context) as response, open(winsw_bin, 'wb') as out_file:
            out_file.write(response.read())
    except Exception as e:
        print(f"ERROR: Failed to download WinSW: {e}", file=sys.stderr)
        sys.exit(1)

    shutil.copy(winsw_bin, rustinel_service_exe)
    shutil.copy(winsw_bin, agent_service_exe)

    # 7. Setup service XMLs
    rustinel_xml = f"""<service>
      <id>RadegastRustinel</id>
      <name>Radegast Rustinel Sensor</name>
      <description>Low-level sensor for the Radegast EDR.</description>
      <executable>{rustinel_core_dir}\\rustinel.exe</executable>
      <arguments>run</arguments>
      <workingdirectory>{agent_dir}</workingdirectory>
      <log mode="roll" logpath="{logs_dir}" />
      <onfailure action="restart" delay="5000" />
      <stopparentfirst>true</stopparentfirst>
      <serviceaccount>
        <domain>NT AUTHORITY</domain>
        <user>SYSTEM</user>
      </serviceaccount>
    </service>"""
    (rustinel_service_dir / "radegast-rustinel-service.xml").write_text(rustinel_xml, encoding="utf-8")

    # Construct PATH containing uv.exe for the agent service
    service_path = f"{python_dir}\\Scripts;{os.environ.get('PATH', '')}"

    agent_xml = f"""<service>
      <id>RadegastAgent</id>
      <name>Radegast EDR Agent</name>
      <description>Management agent for Radegast EDR communications.</description>
      <executable>{agent_exe}</executable>
      <arguments></arguments>
      <workingdirectory>{agent_home_dir}</workingdirectory>
      <env name="PYTHONUNBUFFERED" value="1" />
      <env name="PATH" value="{service_path}" />
      <env name="UV_CACHE_DIR" value="{cache_dir}" />
      <env name="UV_TOOL_DIR" value="{agent_tools_dir}" />
      <env name="UV_TOOL_BIN_DIR" value="{tool_bin_dir}" />
      <env name="LOCALAPPDATA" value="{agent_dir}" />
      <env name="APPDATA" value="{agent_dir}" />
      <env name="RADEGAST_AGENT_BACKEND_URL" value="{backend_url}/api/v1" />
      <env name="RADEGAST_AGENT_DEVICE_TOKEN" value="{token}" />
      <env name="RADEGAST_AGENT_RUSTINEL_BINARY" value="{rustinel_core_dir}\\rustinel.exe" />
      <env name="RADEGAST_AGENT_RUSTINEL_CONFIG" value="{agent_dir}\\config.toml" />
      <env name="RADEGAST_AGENT_RULES_DIR" value="{rules_dir}\\" />
      <env name="RADEGAST_AGENT_ALERTS_DIR" value="{logs_dir}\\" />
      <env name="RADEGAST_AGENT_STATE_DIR" value="{state_dir}\\" />
      <onfailure action="restart" delay="5000" />
      <stopparentfirst>true</stopparentfirst>
      <log mode="roll" logpath="{logs_dir}" />
      <serviceaccount>
        <domain>NT SERVICE</domain>
        <user>RadegastAgent</user>
      </serviceaccount>
    </service>"""
    (agent_service_dir / "radegast-agent-service.xml").write_text(agent_xml, encoding="utf-8")

    # 8. Install Services FIRST
    print("Registering Windows Services...")
    subprocess.run([str(rustinel_service_exe), "install"], check=True)
    subprocess.run([str(agent_service_exe), "install"], check=True)

    print("Waiting for Service Manager to register identities...")
    time.sleep(3)

    # 9. Unblock Files
    print("Clearing Mark of the Web attributes from all files...")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f"Get-ChildItem -Path '{radegast_dir}' -Recurse | Unblock-File"], check=True)

    # 10. Apply Strict NTFS ACLs via icacls
    print("Securing directories with strict layout isolation...")
    vsa_account = r"NT SERVICE\RadegastAgent"

    # A. Lock root directory exclusively to Administrators and SYSTEM
    run_cmd(["icacls", str(radegast_dir), "/inheritance:r", "/grant:r", "Administrators:(OI)(CI)F", "/grant:r",
             "SYSTEM:(OI)(CI)F"], show_output=True)

    # B.Grant the VSA traverse/read rights to the root folder ONLY (No inheritance)
    # Leaving out (OI)(CI) means this rule applies ONLY to the Radegast folder itself.
    # This allows the agent to resolve paths like \Radegast\rustinel\... without being blocked at the root.
    run_cmd(["icacls", str(radegast_dir), "/grant:r", f"{vsa_account}:RX"], show_output=True)

    # C. Grant Full Control exclusively to the requested Agent directory ecosystem
    run_cmd(["icacls", str(agent_dir), "/grant:r", f"{vsa_account}:(OI)(CI)F", "/T", "/Q"], show_output=True)

    # D. Grant Read & Execute (RX) to the Rustinel folder tree AND force it recursively (/T)
    # The /T flag ensures that the *already extracted* rustinel.exe binary instantly receives the RX permission.
    run_cmd(["icacls", str(rustinel_dir), "/grant:r", f"{vsa_account}:(OI)(CI)RX", "/T", "/Q"], show_output=True)

    # Secure global Python directory context to Read & Execute only for the VSA account
    if python_dir.exists():
        run_cmd(["icacls", str(python_dir), "/reset", "/T", "/Q"], show_output=True)
        run_cmd(["icacls", str(python_dir), "/grant:r", f"{vsa_account}:(OI)(CI)RX", "/T", "/Q"], show_output=True)

    # 11. Create Uninstaller Script
    uninstall_bat = radegast_dir / "uninstall.bat"
    uninstall_content = (
        "@echo off\r\n"
        "net session >nul 2>&1\r\n"
        "if errorlevel 1 (\r\n"
        "    echo Requesting administrative privileges...\r\n"
        "    powershell -Command \"Start-Process -FilePath '%~f0' -Verb RunAs\"\r\n"
        "    exit /b 0\r\n"
        ")\r\n"
        "echo WARNING: The signing key cannot be changed and must be backed-up manually if moving to another device.\r\n"
        'set /p "confirm=Have you backed-up your device signing key manually? (y/n): "\r\n'
        'if /i "%confirm%" neq "y" exit /b 1\r\n'
        "echo === Uninstalling Radegast Services ===\r\n"
        f'"{agent_service_exe}" stop >nul 2>&1\r\n'
        f'"{rustinel_service_exe}" stop >nul 2>&1\r\n'
        f'"{agent_service_exe}" uninstall >nul 2>&1\r\n'
        f'"{rustinel_service_exe}" uninstall >nul 2>&1\r\n'
        "taskkill /f /im rustinel.exe >nul 2>&1\r\n"
        "reg delete HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Radegast /f >nul 2>&1\r\n"
        "echo === Removing Files ===\r\n"
        f'start /b "" cmd /c "timeout /t 3 >nul & rmdir /s /q "{radegast_dir}" >nul 2>&1"\r\n'
    )
    uninstall_bat.write_text(uninstall_content, encoding="utf-8")

    # 12. Start Services
    print("Starting Windows Services...")
    try:
        subprocess.run([str(rustinel_service_exe), "start"], check=True)
        subprocess.run([str(agent_service_exe), "start"], check=True)
        print("Services started successfully.")
    except Exception as e:
        print(f"ERROR: Failed to start services: {e}", file=sys.stderr)
        sys.exit(1)

    print("=== Radegast agent & rustinel Windows setup completed successfully ===")


if __name__ == "__main__":
    main()
