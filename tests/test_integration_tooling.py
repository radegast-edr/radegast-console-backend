"""Regression checks for integration artifacts, real encryption, and reporting."""

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from ssage import SSAGE

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/tests"


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("integration_runner", SCRIPTS / "integration_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind,expected", [("upstream", "1.9.0"), ("patched", "1.9.0r2"), ("all", "1.9.0r2")])
def test_select_release_kind(runner, kind, expected):
    releases = [
        {"version": version, "os": os_name, "arch": "amd64"}
        for version in ("1.9.0r2", "1.8.0", "1.9.0", "1.9.0r1")
        for os_name in ("linux", "windows")
    ]
    selected = runner.get_newest_releases_by_platform(releases, kind)
    assert {value["version"] for value in selected.values()} == {expected}
    assert set(selected) == {("linux", "amd64"), ("windows", "amd64")}


def test_release_metadata_preserves_revision_and_rejects_stale_archive(runner, tmp_path):
    archive = tmp_path / "upstream-linux-amd64.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("rustinel", "archive fixture")
    metadata = {"version": "1.8.0r2", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    archive.with_suffix(".json").write_text(json.dumps(metadata))
    assert runner.detect_rustinel_version(archive) == "1.8.0r2"
    archive.write_bytes(b"changed archive")
    with pytest.raises(RuntimeError, match="checksum"):
        runner.detect_rustinel_version(archive)


def test_age_keys_support_real_encryption(runner):
    public_key, private_key = runner.generate_age_keypair_standalone()
    assert SSAGE(private_key).public_key == public_key
    encrypted = SSAGE(private_key).encrypt("integration payload")
    assert SSAGE(private_key).decrypt(encrypted) == "integration payload"


@pytest.mark.parametrize("status", ["PASS", "FAIL"])
def test_cp1252_report_includes_redacted_failure_service_logs(tmp_path, status):
    service_secret = "private-device-token"
    for filename in ("rustinel.service.log", "radegast-agent.log"):
        (tmp_path / filename).write_text(f"Permission denied: alerts.json\nRADEGAST_AGENT_DEVICE_TOKEN => {service_secret}")
    (tmp_path / "alerts.json").write_text("private alert payload")
    diagnostic = "installer failed: " + chr(0x1F9EA)
    (tmp_path / "installer.log").write_text(diagnostic, encoding="utf-8")
    (tmp_path / "runner.log").write_text("test runner stage diagnostics")
    (tmp_path / "test_result.json").write_text(
        json.dumps(
            {
                "status": status,
                "os": "windows",
                "arch": "amd64",
                "detected_version": "1.8.0",
                "failure_stage": "install device" if status == "FAIL" else None,
                "error": diagnostic if status == "FAIL" else None,
            }
        )
    )
    summary = tmp_path / "summary.md"
    environment = {**os.environ, "PYTHONIOENCODING": "cp1252:strict", "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "format_test_summary.py"), "--output-dir", str(tmp_path), "--summary-file", str(summary)],
        env=environment,
        capture_output=True,
        check=True,
    )
    console = result.stdout.decode("cp1252")
    assert "windows/amd64" in console
    assert ("PASSED" if status == "PASS" else "FAILED") in console
    assert service_secret not in console + summary.read_text(encoding="utf-8")
    assert "private alert payload" not in console + summary.read_text(encoding="utf-8")
    if status == "FAIL":
        assert "install device" in console
        assert "test runner stage diagnostics" in console
        assert "installer failed:" in console
        assert "Permission denied: alerts.json" in console
        assert "[REDACTED]" in console
    else:
        assert "test runner stage diagnostics" not in console
        assert "Permission denied: alerts.json" not in console


def test_missing_archive_records_failure_without_running_installer(tmp_path):
    output = tmp_path / "diagnostics"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "integration_test.py"),
            "--rustinel-zip",
            str(tmp_path / "missing.zip"),
            "--output-dir",
            str(output),
            "--os",
            "windows",
            "--arch",
            "amd64",
            "--skip-uninstall",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    data = json.loads((output / "test_result.json").read_text())
    assert data["status"] == "FAIL"
    assert data["failure_stage"] == "resolve release"
    assert "Requested release archive not found" in data["error"]
    assert (output / "failure.log").is_file()
    assert (output / "runner.log").is_file()
    assert not (output / "installer.log").exists()


def test_report_without_result_explains_setup_failure(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "format_test_summary.py"),
            "--output-dir",
            str(tmp_path),
            "--os",
            "mac",
            "--arch",
            "m5",
            "--exit-code",
            "1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "mac/m5" in result.stdout
    assert "No valid test result" in result.stdout
    assert "Cannot read runner.log" in result.stdout


@pytest.mark.parametrize("original", [-1, 2, 3, 4])
def test_linux_runner_perf_preparation(runner, monkeypatch, tmp_path, original):
    policy = tmp_path / "perf_event_paranoid"
    policy.write_text(str(original))
    changes = []
    monkeypatch.setattr(runner, "set_linux_perf_paranoid", changes.append)
    previous = runner.prepare_linux_perf_events(policy)
    assert previous == (original if original >= 3 else None)
    assert changes == ([2] if original >= 3 else [])


def test_failure_result_survives_diagnostic_collection_error(runner, monkeypatch, tmp_path):
    def collect(*args, **kwargs):
        raise OSError("diagnostic permission failure")

    monkeypatch.setattr(runner, "collect_diagnostics", collect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "integration_test.py",
            "--rustinel-zip",
            str(tmp_path / "missing.zip"),
            "--output-dir",
            str(tmp_path),
            "--os",
            "windows",
            "--arch",
            "amd64",
            "--skip-uninstall",
        ],
    )
    assert runner.main() == 1
    result = json.loads((tmp_path / "test_result.json").read_text())
    assert result["failure_stage"] == "resolve release"
    assert "Requested release archive not found" in result["error"]
    assert (tmp_path / "failure.log").is_file()


def test_windows_diagnostics_and_report_include_rustinel_wrapper_logs(runner, monkeypatch, tmp_path):
    program_files = tmp_path / "Program Files"
    root = program_files / "Radegast"
    fixtures = {
        "agent/logs/radegast-rustinel-service.err.log": "Sensor failed: config access denied\nDEVICE_TOKEN=private-token",
        "rustinel/service/radegast-rustinel-service.wrapper.log": "Restarting sensor after exit 1",
        "rustinel/service/radegast-rustinel-service.err.log": "Second sensor log with the same basename",
        "agent/logs/rustinel.log.2026-10-07": "Loaded healthcheck rules",
        "agent/logs/alerts.json.2026-10-07": "private healthcheck alert",
        "agent/rules/sigma/_healthcheck/healthcheck.yml": "CommandLine contains probe-uuid",
        "agent/config.toml": "[logging]\nlevel = 'info'",
        "rustinel/service/radegast-rustinel-service.xml": "<service><id>RadegastRustinel</id></service>",
    }
    for name, content in fixtures.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=f"Diagnostic for {' '.join(command)}", stderr=""),
    )
    output = tmp_path / "diagnostics"
    runner.collect_diagnostics(output, False, "1.9.1", "windows", "amd64", "verify healthcheck pipeline", "Probe timed out")
    assert (output / "radegast-rustinel-service.err.log").read_text() == fixtures["agent/logs/radegast-rustinel-service.err.log"]
    assert "Second sensor log" in (output / "additional-logs/rustinel/service/radegast-rustinel-service.err.log").read_text()
    assert (output / "alerts.json.2026-10-07").is_file()
    assert (output / "rules/sigma/_healthcheck/healthcheck.yml").is_file()
    assert (output / "rustinel-config.toml").is_file()
    assert "queryex RadegastRustinel" in (output / "rustinel.status.log").read_text()
    assert "icacls" in (output / "filesystem_permissions.txt").read_text()

    # Restore the real subprocess API for the formatter process.
    monkeypatch.undo()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "format_test_summary.py"), "--output-dir", str(output), "--exit-code", "1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Sensor failed: config access denied" in result.stdout
    assert "Restarting sensor after exit 1" in result.stdout
    assert "Second sensor log" in result.stdout
    assert "private-token" not in result.stdout
    assert "private healthcheck alert" not in result.stdout


@pytest.mark.parametrize("returncode,state", [(0, "RUNNING"), (0, "STOPPED"), (5, "RUNNING")])
def test_windows_services_must_be_running(runner, monkeypatch, returncode, state):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=state, stderr=""),
    )
    if returncode == 0 and state == "RUNNING":
        runner.verify_services_running("windows")
    else:
        with pytest.raises(RuntimeError, match=r"RadegastRustinel.*not running"):
            runner.verify_services_running("windows")


def test_windows_service_verification_waits_for_startup(runner, monkeypatch):
    states = iter(["RUNNING", "START_PENDING", "RUNNING"])
    queried = []

    def query(command, **kwargs):
        queried.append(command[-1])
        return SimpleNamespace(returncode=0, stdout=next(states), stderr="")

    monkeypatch.setattr(runner.subprocess, "run", query)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    runner.verify_services_running("windows")
    assert queried == ["RadegastRustinel", "RadegastAgent", "RadegastAgent"]


def test_windows_service_startup_wait_is_bounded(runner, monkeypatch):
    clock = iter(range(100))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="START_PENDING", stderr=""),
    )
    with pytest.raises(RuntimeError, match="not running"):
        runner.verify_services_running("windows")


def test_macos_ci_access_is_scoped_and_idempotent(runner, tmp_path):
    database = tmp_path / "TCC.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE access (service TEXT, client TEXT, client_type INTEGER, "
            "auth_value INTEGER, auth_reason INTEGER, auth_version INTEGER, "
            "indirect_object_identifier_type INTEGER, indirect_object_identifier TEXT, "
            "flags INTEGER, last_modified INTEGER, "
            "PRIMARY KEY (service, client, client_type, indirect_object_identifier))"
        )
    runner.grant_macos_full_disk_access(database)
    runner.grant_macos_full_disk_access(database)
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT service, client, client_type, auth_value FROM access").fetchall()
    assert set(rows) == {
        ("kTCCServiceSystemPolicyAllFiles", "io.rustinel.agent", 0, 2),
        ("kTCCServiceSystemPolicyAllFiles", "/Library/Radegast/rustinel/Rustinel.app/Contents/MacOS/rustinel", 1, 2),
        ("kTCCServiceSystemPolicyAllFiles", "/Library/Radegast/rustinel/rustinel", 1, 2),
    }
    with pytest.raises(sqlite3.OperationalError):
        runner.grant_macos_full_disk_access(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()
