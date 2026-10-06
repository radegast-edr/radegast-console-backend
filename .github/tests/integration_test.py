#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "ssage>=0.1.0",
# ]
# ///

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

import httpx
from ssage import SSAGE

from download_releases import get_newest_releases_by_platform
from source_agent import SourceAgent

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("integration-test")


def detect_os() -> str:
    """Detect normalized OS string: 'linux', 'windows', or 'mac'."""
    if sys.platform.startswith("linux"):
        return "linux"
    elif sys.platform.startswith("darwin"):
        return "mac"
    elif sys.platform.startswith("win32"):
        return "windows"
    raise RuntimeError(f"Unsupported operating system: {sys.platform}")


def detect_architecture(os_name: str | None = None) -> str:
    """Detect architecture ('amd64', 'arm64', or 'm5' for macOS Apple Silicon)."""
    target_os = os_name or detect_os()
    machine = platform.machine().lower()
    if target_os == "mac":
        if machine in ("aarch64", "arm64"):
            return "m5"
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return "amd64"


def detect_rustinel_version(zip_path: Path) -> str:
    """Extract and execute rustinel --version to dynamically determine semver."""
    logger.info("Detecting Rustinel version from %s...", zip_path)

    metadata_path = zip_path.with_suffix(".json")
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if hashlib.sha256(zip_path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise RuntimeError(f"Release checksum differs from {metadata_path}")
        return metadata["version"]

    # Preserve the revision when an explicitly supplied archive is in a release directory.
    match = re.search(r"/releases/(\d+\.\d+\.\d+(?:r\d+)?)/", zip_path.as_posix())
    if match:
        return match.group(1)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_path)

        # Look for the rustinel binary (supports rustinel, rustinel.exe, or inside macOS app)
        binary_path = None
        for candidate in tmp_path.rglob("*"):
            if candidate.is_file() and candidate.name.lower() in ("rustinel", "rustinel.exe"):
                binary_path = candidate
                break

        if binary_path and binary_path.exists():
            try:
                binary_path.chmod(0o755)
            except OSError:
                pass
            try:
                result = subprocess.run(
                    [str(binary_path), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                output = f"{result.stdout} {result.stderr}".strip()
                match = re.search(r"(\d+\.\d+\.\d+(?:r\d+)?)", output)
                if match:
                    version = match.group(1)
                    logger.info("Detected Rustinel version via binary: %s", version)
                    return version
            except OSError as e:
                logger.warning("Could not execute rustinel binary for version: %s", e)

        # Check Info.plist if present (macOS bundle)
        for candidate in tmp_path.rglob("Info.plist"):
            try:
                with open(candidate, "rb") as pf:
                    plist_data = plistlib.load(pf)
                    ver = plist_data.get("CFBundleShortVersionString") or plist_data.get("CFBundleVersion")
                    if ver:
                        logger.info("Detected Rustinel version via Info.plist: %s", ver)
                        return str(ver)
            except Exception:
                pass

        # Fallback to inspecting zip filename
        name_match = re.search(r"(\d+\.\d+\.\d+(?:r\d+)?)", zip_path.name)
        if name_match:
            version = name_match.group(1)
            logger.info("Detected Rustinel version via filename: %s", version)
            return version

    default_ver = "1.0.0"
    logger.warning("Falling back to default version: %s", default_ver)
    return default_ver


def generate_age_keypair_standalone() -> tuple[str, str]:
    """Generate AGE recipient and identity strings using cryptography / ssage."""
    private_key = SSAGE.generate_private_key()
    return SSAGE(private_key).public_key, private_key


class BackendManager:
    """Manages the Radegast console backend process during integration test."""

    def __init__(self, backend_dir: Path, port: int = 8000, output_dir: Path | None = None, agent_package: str | None = None):
        self.backend_dir = backend_dir
        self.agent_package = agent_package
        self.port = port
        self.output_dir = output_dir or Path("./test-output")
        self.process: subprocess.Popen | None = None
        tmp_base = Path(tempfile.gettempdir()) / f"radegast_test_backend_{os.getpid()}"
        self.db_path = tmp_base / "radegast_test.db"
        self.uploads_dir = tmp_base / "radegast_uploads"
        self.releases_dir = tmp_base / "radegast_releases"
        self.base_url = f"http://127.0.0.1:{self.port}/api/v1"
        self.log_file = None

    def start(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()

        db_path_posix = self.db_path.resolve().as_posix()
        env = os.environ.copy()
        env["RADEGAST_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path_posix}"
        env["RADEGAST_SECRET_KEY"] = "integration-test-secret-key-1234567890"
        env["RADEGAST_ENVIRONMENT"] = "dev"
        env["RADEGAST_ENABLE_EMAIL_WORKER"] = "False"
        env["RADEGAST_ENABLE_SPACE_USAGE_WORKER"] = "False"
        env["RADEGAST_UPLOAD_DIR"] = str(self.uploads_dir)
        env["RADEGAST_RELEASES_DIR"] = str(self.releases_dir)
        if self.agent_package is not None:
            env["RADEGAST_AGENT_PACKAGE"] = self.agent_package

        uv_bin = shutil.which("uv") or "uv"

        logger.info("Applying database migrations at %s...", self.backend_dir)
        migrations = subprocess.run(
            [uv_bin, "run", "python", "apply-migrations.py"],
            cwd=str(self.backend_dir),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (self.output_dir / "migrations.log").write_text(migrations.stdout + "\n" + migrations.stderr, encoding="utf-8")
        migrations.check_returncode()

        logger.info("Starting uvicorn backend on port %s...", self.port)
        self.log_file = open(self.output_dir / "backend.log", "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                uv_bin,
                "run",
                "uvicorn",
                "app.main:app",
                "--port",
                str(self.port),
                "--host",
                "127.0.0.1",
            ],
            cwd=str(self.backend_dir),
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Wait for backend health
        healthy = False
        for _ in range(30):
            try:
                resp = httpx.get(f"{self.base_url}/health", timeout=1.0)
                if resp.status_code == 200:
                    healthy = True
                    break
            except (httpx.HTTPError, OSError):
                time.sleep(1)

        if not healthy:
            raise RuntimeError("Backend failed to become healthy within 30 seconds.")
        logger.info("Backend is healthy and listening on %s.", self.base_url)

    def stop(self) -> None:
        if self.process:
            logger.info("Stopping backend process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                if sys.platform.startswith("win32"):
                    subprocess.run(["taskkill", "/f", "/t", "/pid", str(self.process.pid)], check=False)
                else:
                    self.process.kill()
        if self.log_file:
            self.log_file.close()


def setup_admin_and_crypto(base_url: str, db_path: Path) -> tuple[str, int]:
    """Register admin, promote via DB, login, setup AGE keys, get default group."""
    email = "admin@example.com"
    password = "AdminPassword123!"

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        logger.info("Registering initial user...")
        resp = client.post("/auth/register", json={"email": email, "password": password})
        if resp.status_code != 200:
            raise RuntimeError(f"User registration failed: {resp.text}")

        # Promote user to admin role and mark verified in SQLite database directly
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("UPDATE users SET role = 'admin', verified = 1 WHERE email = ?", (email,))
        conn.commit()
        conn.close()
        logger.info("Admin user created and verified in database.")

        logger.info("Logging in as admin...")
        resp = client.post("/auth/login", json={"email": email, "password": password})
        if resp.status_code != 200:
            raise RuntimeError(f"Admin login failed: {resp.text}")

        session_cookie = resp.cookies.get("radegast_session") or client.cookies.get("radegast_session")
        if not session_cookie:
            for h in resp.headers.get_list("set-cookie"):
                if "radegast_session=" in h:
                    session_cookie = h.split("radegast_session=")[1].split(";")[0]
                    break
        if not session_cookie:
            token_data = resp.json()
            session_cookie = token_data.get("access_token") or token_data.get("token")
        if not session_cookie:
            raise RuntimeError(f"Failed to obtain session cookie from login: headers={resp.headers} body={resp.text}")

        auth_headers = {
            "Cookie": f"radegast_session={session_cookie}",
            "Authorization": f"Bearer {session_cookie}",
        }
        client.cookies.set("radegast_session", session_cookie)

        # Setup AGE keys
        logger.info("Setting up encryption keys...")
        pub_key, private_key = generate_age_keypair_standalone()
        rec_pub, recovery_key = generate_age_keypair_standalone()
        resp = client.post(
            "/user/keys/setup",
            headers=auth_headers,
            json={
                "public_key": pub_key,
                "recovery_public_key": rec_pub,
                "recovery_encrypted_private_key": SSAGE(recovery_key).encrypt(private_key),
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Keys setup failed: {resp.status_code} - {resp.text}")

        # Fetch teams and groups
        resp = client.get("/teams/", headers=auth_headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list teams: {resp.status_code} - {resp.text}")
        teams = resp.json()
        if not teams:
            raise RuntimeError("No default teams found.")
        team_id = teams[0]["id"]

        resp = client.get(f"/teams/{team_id}/groups", headers=auth_headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list team groups: {resp.status_code} - {resp.text}")
        groups = resp.json()
        if not groups:
            raise RuntimeError("No default groups found for team.")
        group_id = groups[0]["id"]

        return session_cookie, group_id


def upload_release_zip(
    base_url: str,
    session_cookie: str,
    zip_path: Path,
    version: str,
    arch: str,
    os_name: str,
) -> None:
    """Upload the Rustinel release zip via POST /api/v1/releases/."""
    logger.info("Uploading Rustinel release zip (%s, %s/%s)...", version, os_name, arch)
    headers = {
        "Cookie": f"radegast_session={session_cookie}",
        "Authorization": f"Bearer {session_cookie}",
    }
    with open(zip_path, "rb") as f:
        files = {"file": ("rustinel.zip", f, "application/zip")}
        data = {
            "version": version,
            "os": os_name,
            "arch": arch,
        }
        with httpx.Client(base_url=base_url, cookies={"radegast_session": session_cookie}, timeout=30.0) as client:
            resp = client.post("/releases/", headers=headers, data=data, files=files)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to upload release {version}: {resp.status_code} - {resp.text}")
    logger.info("Release %s (%s/%s) uploaded successfully.", version, os_name, arch)


def create_device(base_url: str, session_cookie: str, group_id: int) -> tuple[int, str]:
    """Create a new device via POST /api/v1/devices/."""
    logger.info("Creating new device under group %s...", group_id)
    headers = {
        "Cookie": f"radegast_session={session_cookie}",
        "Authorization": f"Bearer {session_cookie}",
    }
    with httpx.Client(base_url=base_url, cookies={"radegast_session": session_cookie}, timeout=10.0) as client:
        resp = client.post(
            "/devices/",
            headers=headers,
            json={"name": "test-device", "group_id": group_id},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to create device: {resp.status_code} - {resp.text}")
        data = resp.json()
        device_id = data["id"]
        device_token = data["token"]
        logger.info("Device created: id=%s", device_id)
        return device_id, device_token


def grant_macos_full_disk_access(database: Path) -> None:
    """Grant the disposable CI runner's native Endpoint Security prerequisite."""
    clients = (
        ("io.rustinel.agent", 0),
        ("/Library/Radegast/rustinel/Rustinel.app/Contents/MacOS/rustinel", 1),
        ("/Library/Radegast/rustinel/rustinel", 1),
    )
    with sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True) as connection:
        for client, client_type in clients:
            connection.execute(
                "INSERT OR REPLACE INTO access "
                "(service, client, client_type, auth_value, auth_reason, auth_version, "
                "indirect_object_identifier_type, indirect_object_identifier, flags, last_modified) "
                "VALUES (?, ?, ?, 2, 4, 1, 0, 'UNUSED', 0, ?)",
                ("kTCCServiceSystemPolicyAllFiles", client, client_type, int(time.time())),
            )
    logger.info("Provisioned Rustinel Full Disk Access on the disposable macOS CI runner.")


def set_linux_perf_paranoid(value: int) -> None:
    command = ["sysctl", "-w", f"kernel.perf_event_paranoid={value}"]
    if os.geteuid() != 0:
        command = ["sudo", "-H", *command]
    subprocess.run(command, check=True, capture_output=True, text=True)


def prepare_linux_perf_events(sysctl_path: Path = Path("/proc/sys/kernel/perf_event_paranoid")) -> int | None:
    """Allow native perf events on CI kernels with Ubuntu's extra restriction."""
    if not sysctl_path.is_file():
        return None
    original = int(sysctl_path.read_text().strip())
    if original < 3:
        return None
    set_linux_perf_paranoid(2)
    logger.info("Prepared Linux perf events for native eBPF telemetry.")
    return original


def execute_installation(base_url: str, device_token: str, version: str, os_name: str, output_dir: Path) -> None:
    """Download and run the official device install script for target OS."""
    install_url = (
        f"/device/install?os={os_name}&rustinel-version={version}&agent-healthcheck=true"
        "&agent-init-wait-seconds=1&agent-autoupdate=false&rustinel-autoupdate=false"
    )
    logger.info("Fetching official device installation script from %s...", install_url)
    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        resp = client.get(install_url)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch install script: {resp.status_code} - {resp.text}")
        install_script = resp.text

    env = os.environ.copy()
    env["RADEGAST_TOKEN"] = device_token

    if os_name in ("linux", "mac"):
        for var in (
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "UV_CACHE_DIR",
            "UV_CONFIG_DIR",
            "UV_TOOL_DIR",
            "UV_TOOL_BIN_DIR",
            "UV_PYTHON_INSTALL_DIR",
        ):
            env.pop(var, None)
        if os.geteuid() == 0:
            env["HOME"] = "/root" if os_name == "linux" else "/var/root"

    temp_dir = Path(tempfile.gettempdir())

    if os_name == "windows":
        script_path = temp_dir / "install.bat"
        script_path.write_text(install_script, encoding="utf-8")
        logger.info("Executing Windows device installation script: %s...", script_path)
        # On Windows, install.bat self-elevates if needed and runs install-service.py
        result = subprocess.run(
            ["cmd.exe", "/c", str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        (output_dir / "installer.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        logger.info("Installer exited with code %s; output saved to installer.log.", result.returncode)
        if result.returncode != 0:
            raise RuntimeError(f"Device installation script failed with exit code {result.returncode}; see installer.log")

    elif os_name == "mac":
        script_path = temp_dir / "install.sh"
        script_path.write_text(install_script, encoding="utf-8")
        script_path.chmod(0o755)

        logger.info("Executing macOS device installation script as root...")
        cmd = ["bash", str(script_path)]
        if os.name != "nt" and os.geteuid() != 0 and shutil.which("sudo"):
            sudo_prefix = ["sudo", "-H", "env"]
            for k, v in env.items():
                if k not in (
                    "HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_STATE_HOME",
                    "UV_CACHE_DIR",
                    "UV_CONFIG_DIR",
                    "UV_TOOL_DIR",
                    "UV_TOOL_BIN_DIR",
                    "UV_PYTHON_INSTALL_DIR",
                ):
                    sudo_prefix.append(f"{k}={v}")
            cmd = [*sudo_prefix, *cmd]

        result = subprocess.run(cmd, env=env, cwd="/tmp", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        (output_dir / "installer.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        logger.info("Installer exited with code %s; output saved to installer.log.", result.returncode)
        if result.returncode != 0:
            raise RuntimeError(f"Device installation script failed with exit code {result.returncode}")

    else:  # linux
        # Ensure tracefs and debugfs are mounted for Linux eBPF telemetry
        if Path("/sys/kernel").exists():
            subprocess.run(["mkdir", "-p", "/sys/kernel/tracing", "/sys/kernel/debug"], check=False)
            mount_tracefs = ["mount", "-t", "tracefs", "nodev", "/sys/kernel/tracing"]
            mount_debugfs = ["mount", "-t", "debugfs", "nodev", "/sys/kernel/debug"]
            if os.geteuid() != 0 and shutil.which("sudo"):
                subprocess.run(["sudo", "-H", *mount_tracefs], check=False)
                subprocess.run(["sudo", "-H", *mount_debugfs], check=False)
            else:
                subprocess.run(mount_tracefs, check=False)
                subprocess.run(mount_debugfs, check=False)

        script_path = temp_dir / "install.sh"
        script_path.write_text(install_script, encoding="utf-8")
        script_path.chmod(0o755)

        logger.info("Executing Linux device installation script as root...")
        cmd = ["bash", str(script_path)]
        if os.name != "nt" and os.geteuid() != 0 and shutil.which("sudo"):
            sudo_prefix = ["sudo", "-H", "env"]
            for k, v in env.items():
                if k not in (
                    "HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_STATE_HOME",
                    "UV_CACHE_DIR",
                    "UV_CONFIG_DIR",
                    "UV_TOOL_DIR",
                    "UV_TOOL_BIN_DIR",
                    "UV_PYTHON_INSTALL_DIR",
                ):
                    sudo_prefix.append(f"{k}={v}")
            cmd = [*sudo_prefix, *cmd]

        result = subprocess.run(cmd, env=env, cwd="/tmp", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        (output_dir / "installer.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        logger.info("Installer exited with code %s; output saved to installer.log.", result.returncode)
        if result.returncode != 0:
            raise RuntimeError(f"Device installation script failed with exit code {result.returncode}")


def verify_services_running(os_name: str) -> None:
    """Check that rustinel and radegast-agent services are active across platforms."""
    logger.info("Verifying services are running on %s...", os_name)
    if os_name == "linux":
        if not shutil.which("systemctl"):
            raise RuntimeError("systemctl is required for the Linux real-device test.")

        for service in ("rustinel", "radegast-agent"):
            res = subprocess.run(["systemctl", "is-active", "--quiet", service], check=False)
            if res.returncode == 0:
                logger.info("Systemd service '%s' is ACTIVE.", service)
            else:
                raise RuntimeError(f"Service {service!r} is not active; see diagnostics.")

    elif os_name == "windows":
        for service in ("RadegastRustinel", "RadegastAgent"):
            deadline = time.monotonic() + 30
            while True:
                res = subprocess.run(["sc.exe", "query", service], capture_output=True, text=True, check=False)
                if res.returncode == 0 and "RUNNING" in res.stdout:
                    logger.info("Windows service '%s' is RUNNING.", service)
                    break
                if res.returncode == 0 and "START_PENDING" in res.stdout and time.monotonic() < deadline:
                    time.sleep(1)
                    continue
                raise RuntimeError(f"Windows service {service!r} is not running; see diagnostics.\n{res.stdout}\n{res.stderr}")

    elif os_name == "mac":
        for label in ("io.rustinel.daemon", "app.radegast.agent"):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                res = subprocess.run(
                    ["launchctl", "print", f"system/{label}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0 and re.search(r"state\s*=\s*running", res.stdout):
                    logger.info("macOS launchd service '%s' is RUNNING.", label)
                    break
                time.sleep(1)
            else:
                raise RuntimeError(f"macOS service {label!r} is not running; see diagnostics.")


def verify_healthcheck_alert(
    base_url: str,
    session_cookie: str,
    device_id: int,
    timeout_seconds: int = 180,
    expected_agent_version: str | None = None,
) -> bool:
    """Poll backend until the device reports healthy == True."""
    logger.info("Waiting for healthcheck alert to be processed (timeout=%ds)...", timeout_seconds)
    headers = {
        "Cookie": f"radegast_session={session_cookie}",
        "Authorization": f"Bearer {session_cookie}",
    }
    start_time = time.time()

    with httpx.Client(base_url=base_url, cookies={"radegast_session": session_cookie}, timeout=5.0) as client:
        while time.time() - start_time < timeout_seconds:
            try:
                resp = client.get(f"/devices/{device_id}", headers=headers)
                resp.raise_for_status()
                if resp.status_code == 200:
                    device = resp.json()
                    reported_version = device.get("agent_version")
                    if expected_agent_version and reported_version and reported_version != expected_agent_version:
                        raise RuntimeError(f"Service reported agent {reported_version}; expected source agent {expected_agent_version}")
                    is_healthy = device.get("healthy")
                    logger.info(
                        "Device status poll: healthy=%s, last_seen=%s",
                        is_healthy,
                        device.get("last_seen"),
                    )
                    if is_healthy is True and (expected_agent_version is None or reported_version == expected_agent_version):
                        logger.info("Healthcheck alert received and confirmed! Device is HEALTHY.")
                        return True
                    elif is_healthy is False:
                        logger.warning("Device status poll: healthy is explicitly False.")
            except (httpx.HTTPError, OSError) as e:
                logger.warning("Device polling failed: %s", e)
            time.sleep(2)

    logger.error("Healthcheck probe timed out after %d seconds.", timeout_seconds)
    return False


def execute_uninstallation(os_name: str) -> None:
    """Execute official uninstaller to leave the machine in a clean state."""
    logger.info("Executing cleanup uninstallation on %s...", os_name)
    try:
        if os_name == "linux":
            uninst = Path("/opt/radegast/uninstall.sh")
            if uninst.exists():
                cmd = [str(uninst)]
                if os.geteuid() != 0 and shutil.which("sudo"):
                    cmd = ["sudo", "-H", *cmd]
                subprocess.run(cmd, input="y\n", text=True, check=False)
        elif os_name == "mac":
            uninst = Path("/Library/Radegast/uninstall.sh")
            if uninst.exists():
                cmd = [str(uninst)]
                if os.geteuid() != 0 and shutil.which("sudo"):
                    cmd = ["sudo", "-H", *cmd]
                subprocess.run(cmd, input="y\n", text=True, check=False)
        elif os_name == "windows":
            program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            uninst = program_files / "Radegast" / "uninstall.bat"
            if uninst.exists():
                subprocess.run([str(uninst)], input="y\n", text=True, check=False)
    except Exception as e:
        logger.warning("Uninstallation encountered error: %s", e)


def write_test_result(
    output_dir: Path,
    is_success: bool,
    version: str,
    os_name: str,
    arch: str,
    failure_stage: str | None = None,
    error: str | None = None,
) -> None:
    data = {
        "status": "PASS" if is_success else "FAIL",
        "detected_version": version,
        "timestamp": time.time(),
        "os": os_name,
        "arch": arch,
        "failure_stage": failure_stage,
        "error": error,
    }
    (output_dir / "test_result.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def collect_windows_diagnostics(output_dir: Path, program_files: Path) -> None:
    root = program_files / "Radegast"
    agent = root / "agent"
    rustinel = root / "rustinel"

    def capture(command: list[str]) -> str:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
            return f"$ {' '.join(command)}\nExit code: {result.returncode}\n{result.stdout}\n{result.stderr}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"$ {' '.join(command)}\nCannot collect diagnostics: {exc}"

    for service, filename in (("RadegastRustinel", "rustinel.status.log"), ("RadegastAgent", "radegast-agent.status.log")):
        status = "\n".join(capture(["sc.exe", verb, service]) for verb in ("queryex", "qc"))
        (output_dir / filename).write_text(status, encoding="utf-8")

    log_dirs = (
        agent / "logs",
        agent / "service",
        rustinel / "service",
        rustinel / "updater/service",
        rustinel,
        rustinel / "logs",
        rustinel / "rustinel/logs",
    )
    inventory = []
    for directory in log_dirs:
        inventory.append(f"{directory}: {'exists' if directory.is_dir() else 'missing'}")
        for pattern in ("*.log*", "alerts.json*"):
            for source in sorted(directory.glob(pattern)):
                if not source.is_file():
                    continue
                destination = output_dir / source.name
                # Preserve logs with the same basename from separate directories.
                if destination.exists():
                    destination = output_dir / "additional-logs" / source.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source, destination)
                    inventory.append(f"Collected {source} -> {destination.relative_to(output_dir)}")
                except OSError as exc:
                    inventory.append(f"Cannot copy {source}: {exc}")
                    logger.warning("Cannot copy %s: %s", source, exc)

    for source, filename in (
        (agent / "config.toml", "rustinel-config.toml"),
        (rustinel / "service/radegast-rustinel-service.xml", "radegast-rustinel-service.xml"),
    ):
        if source.is_file():
            try:
                shutil.copy2(source, output_dir / filename)
            except OSError as exc:
                inventory.append(f"Cannot copy {source}: {exc}")
    config = agent / "config.toml"
    rules = agent / "rules"
    for pattern in ("*.yml", "*.yaml"):
        for source in rules.rglob(pattern):
            destination = output_dir / "rules" / source.relative_to(rules)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except OSError as exc:
                inventory.append(f"Cannot copy {source}: {exc}")

    for path in (root, agent, config, rules, rules / "sigma/_healthcheck", agent / "logs", rustinel, rustinel / "service"):
        inventory.append(capture(["icacls", str(path)]))
    (output_dir / "filesystem_permissions.txt").write_text("\n\n".join(inventory), encoding="utf-8")


def collect_diagnostics(
    output_dir: Path,
    is_success: bool,
    detected_version: str,
    os_name: str,
    arch: str,
    failure_stage: str | None = None,
    error: str | None = None,
) -> None:
    """Collect service journals, configuration files, and alerts to output directory."""
    logger.info("Collecting test diagnostics to %s...", output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Record identities and exit status without dumping service environments,
    # which contain the device token. Keep the real service manager in charge.
    identity_commands = []
    if os_name in ("linux", "mac"):
        identity_commands = [["id", "radegast-agent" if os_name == "linux" else "_radegast"], ["ps", "-eo", "pid,ppid,user,group,comm"]]
    if os_name == "linux":
        identity_commands.append(
            [
                "systemctl",
                "show",
                "rustinel",
                "radegast-agent",
                "--property=Id,User,Group,MainPID,Result,ExecMainCode,ExecMainStatus,NRestarts,FragmentPath",
            ]
        )
    identity_output = []
    for command in identity_commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            identity_output.append(f"$ {' '.join(command)}\n{result.stdout}\n{result.stderr}")
        except OSError as exc:
            identity_output.append(f"{command[0]} failed: {exc}")
    if identity_output:
        (output_dir / "process_identity.txt").write_text("\n".join(identity_output), encoding="utf-8")

    if os_name == "linux":
        # Capture systemctl status for each service (guaranteed to contain status + recent logs)
        for svc in ("rustinel", "radegast-agent"):
            try:
                res = subprocess.run(
                    ["systemctl", "status", svc, "--no-pager"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                status_text = f"=== systemctl status {svc} ===\n{res.stdout}\n{res.stderr}".strip()
                (output_dir / f"{svc}.status.log").write_text(status_text, encoding="utf-8")
            except OSError as e:
                logger.warning("Failed to collect systemctl status for %s: %s", svc, e)

        # Collect journalctl logs
        if shutil.which("journalctl"):
            for svc in ("rustinel", "radegast-agent"):
                try:
                    res = subprocess.run(
                        ["journalctl", "-u", svc, "--no-pager", "-n", "500"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    log_content = res.stdout.strip()
                    if not log_content or log_content == "-- No entries --":
                        # If journald had no entries, populate from systemctl status output
                        status_file = output_dir / f"{svc}.status.log"
                        if status_file.exists():
                            log_content = status_file.read_text(encoding="utf-8")
                    (output_dir / f"{svc}.service.log").write_text(log_content, encoding="utf-8")
                except OSError as e:
                    logger.warning("Failed to collect journal for %s: %s", svc, e)

        # Collect filesystem layout and permissions
        try:
            ls_out = subprocess.run(
                ["ls", "-la", "/var/log/rustinel", "/etc/rustinel"],
                capture_output=True,
                text=True,
                check=False,
            )
            (output_dir / "filesystem_permissions.txt").write_text(
                f"STDOUT:\n{ls_out.stdout}\nSTDERR:\n{ls_out.stderr}",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Could not list directories: %s", e)

        # Collect all rustinel logs (rustinel.log, rustinel.log.*)
        rustinel_log_dir = Path("/var/log/rustinel")
        if rustinel_log_dir.exists():
            for f in rustinel_log_dir.glob("*.log*"):
                try:
                    shutil.copy2(f, output_dir / f.name)
                except OSError as e:
                    logger.warning("Failed to copy %s: %s", f, e)

        # Collect alerts.json and dated variants
        if rustinel_log_dir.exists():
            for f in rustinel_log_dir.glob("alerts.json*"):
                try:
                    shutil.copy2(f, output_dir / f.name)
                except Exception as e:
                    logger.warning("Failed to copy %s: %s", f, e)

        # Collect rules
        rules_dir = Path("/etc/rustinel/rules")
        if rules_dir.exists():
            for f in rules_dir.rglob("*.yml"):
                try:
                    dest = output_dir / "rules" / f.relative_to(rules_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                except OSError:
                    pass

    elif os_name == "mac":
        for label, out_name in (
            ("app.radegast.agent", "radegast-agent.status.log"),
            ("io.rustinel.daemon", "rustinel.status.log"),
        ):
            try:
                res = subprocess.run(
                    ["launchctl", "print", f"system/{label}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                (output_dir / out_name).write_text(f"=== launchctl list {label} ===\n{res.stdout}\n{res.stderr}".strip(), encoding="utf-8")
            except OSError as e:
                logger.warning("Failed to collect launchctl status for %s: %s", label, e)

        mac_log_dir = Path("/Library/Logs/Radegast")
        if mac_log_dir.exists():
            for f in mac_log_dir.glob("*.log*"):
                try:
                    shutil.copy2(f, output_dir / f.name)
                except OSError as e:
                    logger.warning("Failed to copy %s: %s", f, e)
            for f in mac_log_dir.glob("alerts.json*"):
                try:
                    shutil.copy2(f, output_dir / f.name)
                except OSError as e:
                    logger.warning("Failed to copy %s: %s", f, e)

        rules_dir = Path("/Library/Radegast/etc/rules")
        if rules_dir.exists():
            for f in rules_dir.rglob("*.yml"):
                try:
                    dest = output_dir / "rules" / f.relative_to(rules_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                except OSError:
                    pass

        try:
            ls_out = subprocess.run(
                ["ls", "-la", "/Library/Radegast", "/Library/Logs/Radegast"],
                capture_output=True,
                text=True,
                check=False,
            )
            (output_dir / "filesystem_permissions.txt").write_text(
                f"STDOUT:\n{ls_out.stdout}\nSTDERR:\n{ls_out.stderr}",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Could not list directories: %s", e)

    elif os_name == "windows":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        collect_windows_diagnostics(output_dir, program_files)

    write_test_result(output_dir, is_success, detected_version, os_name, arch, failure_stage, error)

    # Ensure all files and subdirectories are world-readable for summary generator
    for p in output_dir.rglob("*"):
        try:
            if p.is_file():
                p.chmod(0o644)
            elif p.is_dir():
                p.chmod(0o755)
        except OSError:
            pass
    try:
        output_dir.chmod(0o755)
    except OSError:
        pass


def fetch_newest_rustinel_release(
    target_os: str,
    target_arch: str,
    cache_dir: Path | None = None,
    api_base: str = "https://console-api.radegast.app",
) -> tuple[Path, str]:
    """Fetch the newest rustinel release zip from the public API for the specified platform."""
    cache = cache_dir or Path(tempfile.gettempdir()) / "radegast_test_cache"
    cache.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "radegast-test-runner/1.0"}

    url = f"{api_base.rstrip('/')}/api/v1/public/releases/"
    logger.info("Querying releases from %s for %s/%s...", url, target_os, target_arch)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        items = resp.json()

    newest = get_newest_releases_by_platform(items).get((target_os, target_arch))
    if newest is None:
        raise RuntimeError(f"No release found for {target_os}/{target_arch} at {url}")
    version = newest["version"]

    zip_path = cache / f"rustinel-{version}-{target_os}-{target_arch}.zip"
    if zip_path.exists() and zipfile.is_zipfile(zip_path):
        logger.info("Using cached Rustinel release: %s (version %s)", zip_path, version)
        return zip_path, version

    download_url = f"{api_base.rstrip('/')}/api/v1/public/releases/{version}/{target_os}/{target_arch}/download"
    logger.info("Downloading newest Rustinel release v%s from %s...", version, download_url)
    temp_zip = zip_path.with_suffix(".tmp")
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        with client.stream("GET", download_url, headers=headers) as dl_resp:
            dl_resp.raise_for_status()
            with open(temp_zip, "wb") as f:
                for chunk in dl_resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)

    temp_zip.replace(zip_path)
    logger.info("Downloaded %s (size %d bytes)", zip_path, zip_path.stat().st_size)
    return zip_path, version


def locate_backend_dir(candidate_path: Path | None) -> Path:
    """Locate radegast-console-backend directory or clone it if missing."""
    candidates = []
    if candidate_path:
        candidates.append(candidate_path)

    env_path = os.environ.get("RADEGAST_BACKEND_DIR")
    if env_path:
        candidates.append(Path(env_path))

    candidates.extend(
        [
            Path(__file__).resolve().parents[2],
            Path(__file__).resolve().parent.parent,
            Path.cwd(),
            Path("/opt/radegast-console-backend"),
            Path("/tmp/test-backend"),
            Path(__file__).resolve().parents[3] / "radegast-console-backend",
            Path(__file__).resolve().parent.parent.parent / "radegast-console-backend",
            Path.cwd() / "radegast-console-backend",
        ]
    )

    for c in candidates:
        if c.exists() and (c / "app" / "main.py").exists():
            logger.info("Found backend directory at: %s", c)
            return c

    # Clone to a cache directory
    clone_dir = Path(tempfile.gettempdir()) / "radegast-console-backend"
    if not (clone_dir / "app" / "main.py").exists():
        logger.info("Backend not found locally; cloning from GitHub into %s...", clone_dir)
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://github.com/radegast-edr/radegast-console-backend.git",
                str(clone_dir),
            ],
            check=True,
        )
        uv_bin = shutil.which("uv") or "uv"
        subprocess.run([uv_bin, "sync", "--frozen"], cwd=str(clone_dir), check=True)

    return clone_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-device cross-platform integration test runner")
    parser.add_argument(
        "--rustinel-zip",
        type=Path,
        default=Path("/input/rustinel.zip"),
        help="Path to rustinel release zip file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./test-output"),
        help="Path to output diagnostics directory",
    )
    parser.add_argument(
        "--backend-dir",
        type=Path,
        default=None,
        help="Path to radegast-console-backend source",
    )
    parser.add_argument(
        "--version",
        type=str,
        default=None,
        help="Optional explicit Rustinel version string",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout for healthcheck alert reception in seconds",
    )
    parser.add_argument(
        "--os",
        dest="os_name",
        type=str,
        default=None,
        help="Target OS ('linux', 'windows', 'mac') - auto-detected by default",
    )
    parser.add_argument(
        "--arch",
        dest="arch",
        type=str,
        default=None,
        help="Target architecture ('amd64', 'arm64', 'm5') - auto-detected by default",
    )
    parser.add_argument(
        "--skip-uninstall",
        action="store_true",
        help="Skip uninstallation cleanup step after test",
    )
    parser.add_argument("--agent-source", type=Path, help="Build and install this agent checkout instead of the PyPI package")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runner_log_handler = logging.FileHandler(args.output_dir / "runner.log", mode="w", encoding="utf-8")
    runner_log_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(runner_log_handler)

    os_name = args.os_name or detect_os()
    arch = args.arch or detect_architecture(os_name)
    version = args.version or "unknown"
    backend = None
    source_agent = None
    is_success = False
    original_perf_paranoid = None
    stage = "resolve release"
    failure_stage = None
    error = None
    write_test_result(args.output_dir, False, version, os_name, arch, stage, "Test has not completed")

    try:
        logger.info("Real-device integration test: %s/%s", os_name, arch)
        if not args.rustinel_zip.exists():
            if args.rustinel_zip != Path("/input/rustinel.zip"):
                raise FileNotFoundError(f"Requested release archive not found: {args.rustinel_zip}")
            args.rustinel_zip, downloaded_version = fetch_newest_rustinel_release(os_name, arch)
            version = args.version or downloaded_version
        else:
            version = args.version or detect_rustinel_version(args.rustinel_zip)
        logger.info("Evaluating release %s from %s", version, args.rustinel_zip)

        stage = "prepare runner"
        if os_name == "linux":
            original_perf_paranoid = prepare_linux_perf_events()
        elif os_name == "mac" and sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true":
            grant_macos_full_disk_access(Path("/Library/Application Support/com.apple.TCC/TCC.db"))
        if args.agent_source is not None:
            stage = "build source agent"
            source_agent = SourceAgent(args.agent_source, args.output_dir)
            source_agent.start()
        stage = "start backend"
        backend = BackendManager(
            backend_dir=locate_backend_dir(args.backend_dir),
            output_dir=args.output_dir,
            agent_package=source_agent.package_url if source_agent else None,
        )
        backend.start()
        stage = "configure account and encryption"
        session_cookie, group_id = setup_admin_and_crypto(backend.base_url, backend.db_path)
        stage = "upload release"
        upload_release_zip(backend.base_url, session_cookie, args.rustinel_zip, version, arch, os_name)
        stage = "create device"
        device_id, device_token = create_device(backend.base_url, session_cookie, group_id)
        stage = "install device"
        execute_installation(backend.base_url, device_token, version, os_name, args.output_dir)
        stage = "verify services"
        verify_services_running(os_name)
        if source_agent is not None:
            stage = "verify installed source agent"
            source_agent.verify_installed(os_name)
        stage = "verify healthcheck pipeline"
        is_success = verify_healthcheck_alert(
            backend.base_url,
            session_cookie,
            device_id,
            timeout_seconds=args.timeout,
            expected_agent_version=source_agent.manifest["version"] if source_agent else None,
        )
        if not is_success:
            raise RuntimeError(f"No successful native healthcheck reached the backend within {args.timeout} seconds")
    except Exception as exc:
        failure_stage = stage
        error = str(exc)
        (args.output_dir / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        logger.exception("Integration test failed during %s", stage)
    finally:
        write_test_result(args.output_dir, is_success, version, os_name, arch, failure_stage, error)
        try:
            collect_diagnostics(args.output_dir, is_success, version, os_name, arch, failure_stage, error)
        except Exception:
            logger.exception("Diagnostic collection failed; test_result.json and runner.log are available")
        finally:
            try:
                if not args.skip_uninstall and backend is not None:
                    execute_uninstallation(os_name)
            finally:
                try:
                    if backend is not None:
                        backend.stop()
                finally:
                    if source_agent is not None:
                        source_agent.stop()
                    if original_perf_paranoid is not None:
                        set_linux_perf_paranoid(original_perf_paranoid)
                    logging.getLogger().removeHandler(runner_log_handler)
                    runner_log_handler.close()

    logger.info("Integration test %s.", "PASSED" if is_success else "FAILED")
    return 0 if is_success else 1


if __name__ == "__main__":
    sys.exit(main())
