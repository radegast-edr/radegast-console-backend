import os
import sys
import base64
import platform
import urllib.request
import zipfile
import subprocess
import shutil
import time
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

    # Define core paths
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    radegast_dir = Path(program_files) / "Radegast"
    rustinel_dir = radegast_dir / "rustinel"
    rules_dir = radegast_dir / "rules"
    ioc_dir = rules_dir / "ioc"
    logs_dir = radegast_dir / "logs"
    state_dir = radegast_dir / "state"
    cache_dir = radegast_dir / ".cache"
    tools_dir = radegast_dir / ".tools"
    agent_src_dir = radegast_dir / "agent-src"

    python_exe_path = Path(sys.executable)
    python_dir = python_exe_path.parent
    rustinel_service_exe = radegast_dir / "radegast-rustinel-service.exe"
    agent_service_exe = radegast_dir / "radegast-agent-service.exe"

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
    print(f"Creating directories under {radegast_dir}...")
    for d in [radegast_dir, rustinel_dir, rules_dir, ioc_dir, logs_dir, state_dir, cache_dir, tools_dir, agent_src_dir]:
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
    zip_path = rustinel_dir / "rustinel.zip"

    print("Downloading rustinel...")
    try:
        req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
        import ssl
        ssl_context = ssl._create_unverified_context() if hasattr(ssl, '_create_unverified_context') else None
        with urllib.request.urlopen(req, context=ssl_context) as response, open(zip_path, 'wb') as out_file:
            out_file.write(response.read())

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(rustinel_dir)
        if zip_path.exists():
            zip_path.unlink()
    except Exception as e:
        print(f"ERROR: Failed to download/extract rustinel: {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Write configuration file config.toml
    config_b64 = "{{ config_b64 }}"
    config_content = base64.b64decode(config_b64.encode("utf-8")).decode("utf-8")
    (rustinel_dir / "config.toml").write_text(config_content, encoding="utf-8")

    # 5. Install radegast-agent-python
    agent_zip_url = "https://github.com/radegast-edr/radegast-agent-python/archive/refs/heads/main.zip"
    agent_zip_path = agent_src_dir / "agent.zip"

    print("Downloading and installing Python agent...")
    try:
        req = urllib.request.Request(agent_zip_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ssl_context) as response, open(agent_zip_path, 'wb') as out_file:
            out_file.write(response.read())
        with zipfile.ZipFile(agent_zip_path, 'r') as zip_ref:
            zip_ref.extractall(agent_src_dir)
    except Exception as e:
        print(f"ERROR: Failed to download/extract agent source: {e}", file=sys.stderr)
        sys.exit(1)

    uv_exe = python_dir / "Scripts" / "uv.exe"
    agent_exe = python_dir / "Scripts" / "radegast-agent.exe"

    subprocess.run([str(uv_exe), "pip", "install", "--python", str(python_exe_path),
                    str(agent_src_dir / "radegast-agent-python-main")], check=True)
    shutil.rmtree(agent_src_dir, ignore_errors=True)

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
      <executable>{rustinel_dir}\\rustinel.exe</executable>
      <arguments>run</arguments>
      <workingdirectory>{rustinel_dir}</workingdirectory>
      <log mode="roll" logpath="{logs_dir}" />
      <onfailure action="restart" delay="5000" />
      <stopparentfirst>true</stopparentfirst>
      <serviceaccount>
        <domain>NT AUTHORITY</domain>
        <user>SYSTEM</user>
      </serviceaccount>
    </service>"""
    (radegast_dir / "radegast-rustinel-service.xml").write_text(rustinel_xml, encoding="utf-8")

    agent_xml = f"""<service>
      <id>RadegastAgent</id>
      <name>Radegast EDR Agent</name>
      <description>Management agent for Radegast EDR communications.</description>
      <executable>{python_exe_path}</executable>
      <arguments>"{python_dir}\\Lib\\site-packages\\agent\\cli.py"</arguments>
      <workingdirectory>{radegast_dir}</workingdirectory>
      <env name="PYTHONUNBUFFERED" value="1" />
      <env name="RADEGAST_AGENT_BACKEND_URL" value="{backend_url}/api/v1" />
      <env name="RADEGAST_AGENT_DEVICE_TOKEN" value="{token}" />
      <env name="RADEGAST_AGENT_RUSTINEL_BINARY" value="{rustinel_dir}\\rustinel.exe" />
      <env name="RADEGAST_AGENT_RULES_DIR" value="{rules_dir}\\" />
      <env name="RADEGAST_AGENT_ALERTS_DIR" value="{logs_dir}\\" />
      <env name="RADEGAST_AGENT_STATE_DIR" value="{state_dir}\\" />
      <onfailure action="restart" delay="5000" />
      <stopparentfirst>true</stopparentfirst>
      <log mode="roll" logpath="{logs_dir}" />
      <serviceaccount>
        <domain>NT AUTHORITY</domain>
        <user>SYSTEM</user>
      </serviceaccount>
    </service>"""
    (radegast_dir / "radegast-agent-service.xml").write_text(agent_xml, encoding="utf-8")

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
    print("Securing directories with strict ACLs...")
    vsa_account = r"NT SERVICE\RadegastAgent"

    run_cmd(["icacls", str(radegast_dir), "/inheritance:r", "/grant:r", "Administrators:(OI)(CI)F", "/grant:r",
             "SYSTEM:(OI)(CI)F"], show_output=True)
    run_cmd(["icacls", str(radegast_dir), "/grant:r", f"{vsa_account}:(OI)(CI)RX"], show_output=True)

    for folder in [rules_dir, state_dir, logs_dir, cache_dir, python_dir]:
        if folder.exists():
            run_cmd(["icacls", str(folder), "/reset", "/T", "/Q"], show_output=True)
            run_cmd(["icacls", str(folder), "/grant:r", f"{vsa_account}:(OI)(CI)F", "/T", "/Q"], show_output=True)

    run_cmd(["icacls", str(rustinel_dir), "/deny", f"{vsa_account}:(OI)(CI)W"], show_output=True)

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
        "set /p \"confirm=Have you backed-up your device signing key manually? (y/n): \"\r\n"
        "if /i \"%confirm%\" neq \"y\" exit /b 1\r\n"
        "echo === Uninstalling Radegast Services ===\r\n"
        f"\"{radegast_dir}\\radegast-agent-service.exe\" stop >nul 2>&1\r\n"
        f"\"{radegast_dir}\\radegast-rustinel-service.exe\" stop >nul 2>&1\r\n"
        f"\"{radegast_dir}\\radegast-agent-service.exe\" uninstall >nul 2>&1\r\n"
        f"\"{radegast_dir}\\radegast-rustinel-service.exe\" uninstall >nul 2>&1\r\n"
        "taskkill /f /im rustinel.exe >nul 2>&1\r\n"
        "reg delete HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Radegast /f >nul 2>&1\r\n"
        "echo === Removing Files ===\r\n"
        f"start /b \"\" cmd /c \"timeout /t 3 >nul & rmdir /s /q \"{radegast_dir}\" >nul 2>&1\"\r\n"
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
