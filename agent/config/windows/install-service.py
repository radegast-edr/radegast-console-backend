import os
import sys
import base64
import platform
import urllib.request
import zipfile
import subprocess
from pathlib import Path

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
        print("Please run: set RADEGAST_TOKEN=your_token and then run the installer.", file=sys.stderr)
        sys.exit(1)

    # 1. Setup Directories
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    radegast_dir = Path(program_files) / "Radegast"
    rustinel_dir = radegast_dir / "rustinel"
    rules_dir = radegast_dir / "rules"
    logs_dir = radegast_dir / "logs"
    state_dir = radegast_dir / "state"
    cache_dir = radegast_dir / ".cache"
    tools_dir = radegast_dir / ".tools"
    agent_src_dir = radegast_dir / "agent-src"

    print(f"Creating directories under {radegast_dir}...")
    for d in [radegast_dir, rustinel_dir, rules_dir, logs_dir, state_dir, cache_dir, tools_dir, agent_src_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 2. Get architecture and download rustinel
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        print(f"ERROR: Unsupported architecture: {machine}", file=sys.stderr)
        sys.exit(1)

    backend_url = "{{ backend_url }}"
    download_url = f"{backend_url}/api/v1/device/agent/download?os=windows&arch={arch}"
    zip_path = rustinel_dir / "rustinel.zip"

    print(f"Downloading rustinel from {download_url}...")
    try:
        # Use urllib.request with a proper User-Agent to avoid issues
        req = urllib.request.Request(
            download_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        import ssl
        ssl_context = ssl._create_unverified_context() if hasattr(ssl, '_create_unverified_context') else None
        with urllib.request.urlopen(req, context=ssl_context) as response:
            with open(zip_path, 'wb') as out_file:
                out_file.write(response.read())
    except Exception as e:
        print(f"ERROR: Failed to download rustinel: {e}", file=sys.stderr)
        sys.exit(1)

    print("Extracting rustinel...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(rustinel_dir)
    except Exception as e:
        print(f"ERROR: Failed to extract rustinel: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass

    # 3. Write configuration file config.toml
    config_b64 = "{{ config_b64 }}"
    config_content = base64.b64decode(config_b64.encode("utf-8")).decode("utf-8")
    config_path = rustinel_dir / "config.toml"
    print(f"Writing configuration to {config_path}...")
    config_path.write_text(config_content, encoding="utf-8")

    # 4. Install radegast-agent-python by downloading and extracting ZIP, then running uv pip install
    agent_zip_url = "https://github.com/radegast-edr/radegast-agent-python/archive/refs/heads/main.zip"
    agent_zip_path = agent_src_dir / "agent.zip"
    
    print(f"Downloading agent source from {agent_zip_url}...")
    try:
        req = urllib.request.Request(
            agent_zip_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        import ssl
        ssl_context = ssl._create_unverified_context() if hasattr(ssl, '_create_unverified_context') else None
        with urllib.request.urlopen(req, context=ssl_context) as response:
            with open(agent_zip_path, 'wb') as out_file:
                out_file.write(response.read())
    except Exception as e:
        print(f"ERROR: Failed to download agent source: {e}", file=sys.stderr)
        sys.exit(1)

    print("Extracting agent source...")
    try:
        with zipfile.ZipFile(agent_zip_path, 'r') as zip_ref:
            zip_ref.extractall(agent_src_dir)
    except Exception as e:
        print(f"ERROR: Failed to extract agent source: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if agent_zip_path.exists():
            try:
                agent_zip_path.unlink()
            except Exception:
                pass

    print("Installing radegast-agent-python into Python environment...")
    python_exe = Path(sys.executable)
    uv_exe = python_exe.parent / "Scripts" / "uv.exe"
    try:
        subprocess.run([
            str(uv_exe), "pip", "install", 
            "--python", str(python_exe), 
            str(agent_src_dir / "radegast-agent-python-main")
        ], check=True)
    except Exception as e:
        print(f"ERROR: Failed to install agent: {e}", file=sys.stderr)
        sys.exit(1)

    # Clean up agent source folder
    import shutil
    try:
        shutil.rmtree(agent_src_dir)
    except Exception:
        pass

    # 5. Create wrapper batch scripts
    run_rustinel_bat = radegast_dir / "run-rustinel.bat"
    run_rustinel_content = f'@echo off\r\ncd /d "{rustinel_dir}"\r\n"{rustinel_dir}\\rustinel.exe" run > "{logs_dir}\\rustinel_stdout.log" 2> "{logs_dir}\\rustinel_stderr.log"\r\n'
    print(f"Writing {run_rustinel_bat}...")
    run_rustinel_bat.write_text(run_rustinel_content, encoding="utf-8")

    run_agent_bat = radegast_dir / "run-agent.bat"
    run_agent_content = (
        f'@echo off\r\n'
        f'set PYTHONUNBUFFERED=1\r\n'
        f'set RADEGAST_AGENT_BACKEND_URL={backend_url}/api/v1\r\n'
        f'set RADEGAST_AGENT_DEVICE_TOKEN={token}\r\n'
        f'set RADEGAST_AGENT_RUSTINEL_BINARY={rustinel_dir}\\rustinel.exe\r\n'
        f'set RADEGAST_AGENT_RULES_DIR={rules_dir}\\\r\n'
        f'set RADEGAST_AGENT_ALERTS_DIR={logs_dir}\\\r\n'
        f'set RADEGAST_AGENT_STATE_DIR={state_dir}\\\r\n'
        f'"{python_exe.parent}\\Scripts\\radegast-agent.exe" > "{logs_dir}\\agent_stdout.log" 2> "{logs_dir}\\agent_stderr.log"\r\n'
    )
    print(f"Writing {run_agent_bat}...")
    run_agent_bat.write_text(run_agent_content, encoding="utf-8")

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
        "if /i \"%confirm%\" neq \"y\" (\r\n"
        "    echo Uninstallation cancelled.\r\n"
        "    exit /b 1\r\n"
        ")\r\n"
        "echo === Starting Radegast EDR Agent and Rustinel Windows Uninstallation ===\r\n"
        "schtasks /query /tn \"RadegastRustinel\" >nul 2>&1\r\n"
        "if not errorlevel 1 (\r\n"
        "    schtasks /end /tn \"RadegastRustinel\" >nul 2>&1\r\n"
        "    schtasks /delete /tn \"RadegastRustinel\" /f >nul 2>&1\r\n"
        ")\r\n"
        "schtasks /query /tn \"RadegastAgent\" >nul 2>&1\r\n"
        "if not errorlevel 1 (\r\n"
        "    schtasks /end /tn \"RadegastAgent\" >nul 2>&1\r\n"
        "    schtasks /delete /tn \"RadegastAgent\" /f >nul 2>&1\r\n"
        ")\r\n"
        "taskkill /f /im rustinel.exe >nul 2>&1\r\n"
        "reg delete HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Radegast /f >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\rustinel\" >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\rules\" >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\logs\" >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\state\" >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\.cache\" >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\.tools\" >nul 2>&1\r\n"
        "rmdir /s /q \"" + str(radegast_dir) + "\\python\" >nul 2>&1\r\n"
        "del /f /q \"" + str(radegast_dir) + "\\run-rustinel.bat\" >nul 2>&1\r\n"
        "del /f /q \"" + str(radegast_dir) + "\\run-agent.bat\" >nul 2>&1\r\n"
        "echo === Radegast EDR Agent and Rustinel uninstalled successfully ===\r\n"
        "start /b \"\" cmd /c \"timeout /t 2 >nul & del /f /q \"%~f0\" & rmdir /s /q \"" + str(radegast_dir) + "\" >nul 2>&1\"\r\n"
    )
    print(f"Writing {uninstall_bat}...")
    uninstall_bat.write_text(uninstall_content, encoding="utf-8")

    # 6. Setup Windows Scheduled Tasks using PowerShell (to configure power and duration settings)
    ps_script = """
    # Unblock all files recursively to prevent SmartScreen/Mark of the Web silent hangs
    Get-ChildItem -Path '{radegast_dir}' -Recurse | Unblock-File

    # Unregister existing tasks if they exist
    if (Get-ScheduledTask -TaskName 'RadegastRustinel' -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName 'RadegastRustinel' -Confirm:$false -ErrorAction SilentlyContinue
    }
    if (Get-ScheduledTask -TaskName 'RadegastAgent' -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName 'RadegastAgent' -Confirm:$false -ErrorAction SilentlyContinue
    }

    # Register RadegastRustinel task via cmd.exe
    $action1 = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c ""{run_rustinel_bat}""'
    $trigger1 = New-ScheduledTaskTrigger -AtStartup
    $settings1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'RadegastRustinel' -Action $action1 -Trigger $trigger1 -Settings $settings1 -User 'SYSTEM' -Force

    # Register RadegastAgent task via cmd.exe
    $action2 = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c ""{run_agent_bat}""'
    $trigger2 = New-ScheduledTaskTrigger -AtStartup
    $settings2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'RadegastAgent' -Action $action2 -Trigger $trigger2 -Settings $settings2 -User 'SYSTEM' -Force

    # Start tasks immediately
    Start-ScheduledTask -TaskName 'RadegastRustinel'
    Start-ScheduledTask -TaskName 'RadegastAgent'
    """.replace("{radegast_dir}", str(radegast_dir)).replace("{run_rustinel_bat}", str(run_rustinel_bat)).replace("{run_agent_bat}", str(run_agent_bat))

    print("Configuring and starting tasks via PowerShell...")
    try:
        encoded_ps = base64.b64encode(ps_script.encode('utf-16-le')).decode('ascii')
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_ps], check=True)
        print("Scheduled Tasks registered and started successfully.")
    except Exception as e:
        print(f"ERROR: Failed to configure tasks via PowerShell: {e}", file=sys.stderr)
        sys.exit(1)

    # 7. Register Radegast in Add/Remove Programs
    print("Registering Radegast in Add/Remove Programs...")
    try:
        registry_entries = [
            ("DisplayName", "REG_SZ", "Radegast EDR Agent"),
            ("UninstallString", "REG_SZ", f'"{uninstall_bat}"'),
            ("InstallLocation", "REG_SZ", str(radegast_dir)),
            ("Publisher", "REG_SZ", "Radegast"),
            ("NoModify", "REG_DWORD", "1"),
            ("NoRepair", "REG_DWORD", "1")
        ]
        for val_name, val_type, val_data in registry_entries:
            subprocess.run([
                "reg", "add", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Radegast",
                "/v", val_name, "/t", val_type, "/d", val_data, "/f"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Registered successfully in Add/Remove Programs.")
    except Exception as e:
        print(f"WARNING: Failed to register in Add/Remove Programs: {e}")

    print("=== Radegast agent & rustinel Windows setup completed successfully ===")

if __name__ == "__main__":
    main()
