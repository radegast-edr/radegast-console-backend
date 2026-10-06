"""Check native platform selection and subprocess boundaries for the shared action."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def action():
    spec = importlib.util.spec_from_file_location("integration_action", ROOT / ".github/actions/rustinel-integration/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "system,machine,os_name,arch",
    [
        ("Linux", "x86_64", "linux", "amd64"),
        ("Linux", "aarch64", "linux", "arm64"),
        ("Darwin", "arm64", "mac", "m5"),
        ("Darwin", "x86_64", "mac", "amd64"),
        ("Windows", "AMD64", "windows", "amd64"),
    ],
)
def test_candidate_selection_uses_native_architecture(action, monkeypatch, tmp_path, system, machine, os_name, arch):
    monkeypatch.setattr(action.platform, "system", lambda: system)
    monkeypatch.setattr(action.platform, "machine", lambda: machine)
    values = action.prepare(
        {
            "ACTION_DIR": str(ROOT / ".github/actions/rustinel-integration"),
            "RUNNER_TEMP": str(tmp_path),
            "INPUT_ASSET_DIR": str(tmp_path / "candidate assets"),
            "INPUT_AGENT_SOURCE": str(tmp_path / "agent checkout" / ".." / "agent checkout"),
        }
    )
    assert values["backend-dir"] == str(ROOT)
    assert values["agent-source"] == str(tmp_path / "agent checkout")
    assert values["os"] == os_name
    assert values["arch"] == arch
    assert values["download"] == "false"
    assert values["rustinel-zip"] == str(tmp_path / "candidate assets" / f"upstream-{os_name}-{arch}.zip")


def test_auto_download_and_explicit_archive_are_distinct(action, tmp_path):
    env = {"ACTION_DIR": str(ROOT / ".github/actions/rustinel-integration"), "RUNNER_TEMP": str(tmp_path)}
    assert action.prepare(env)["download"] == "true"
    env["INPUT_RUSTINEL_ZIP"] = str(tmp_path / "missing candidate.zip")
    assert action.prepare(env)["download"] == "false"
    env["INPUT_ASSET_DIR"] = str(tmp_path)
    with pytest.raises(ValueError, match="not both"):
        action.prepare(env)


@pytest.mark.parametrize("os_name", ["linux", "mac", "windows"])
def test_privilege_boundary_preserves_candidate_and_paths(action, monkeypatch, tmp_path, os_name):
    monkeypatch.setattr(action.shutil, "which", lambda name: "/tools with spaces/uv")
    env = {
        "BACKEND_DIR": str(tmp_path / "backend source"),
        "RUSTINEL_ZIP": str(tmp_path / "candidate.zip"),
        "OUTPUT_DIR": str(tmp_path / "test output"),
        "TEST_OS": os_name,
        "TEST_ARCH": "m5" if os_name == "mac" else "amd64",
        "TEST_TIMEOUT": "15",
        "TEST_VERSION": "1.8.0r3",
        "AGENT_SOURCE": str(tmp_path / "agent source checkout"),
        "PATH": "/tools with spaces:/usr/bin",
    }
    command = action.test_command(env)
    if os_name == "windows":
        assert command[0] == "/tools with spaces/uv"
        assert "sudo" not in command
    else:
        assert command[:4] == ["sudo", "env", f"PATH={env['PATH']}", "GITHUB_ACTIONS=true"]
    for option, value in (
        ("--project", env["BACKEND_DIR"]),
        ("--backend-dir", env["BACKEND_DIR"]),
        ("--rustinel-zip", env["RUSTINEL_ZIP"]),
        ("--version", "1.8.0r3"),
        ("--agent-source", env["AGENT_SOURCE"]),
    ):
        assert command[command.index(option) + 1] == value
    assert "chmod" not in command


def test_runner_failure_is_returned_without_candidate_download(action, monkeypatch, tmp_path):
    env = {"BACKEND_DIR": str(ROOT), "DOWNLOAD": "false"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["run.py", "test"])
    monkeypatch.setattr(action, "test_command", lambda env: ["candidate-test"])
    monkeypatch.setattr(action.subprocess, "call", lambda command: 23)
    monkeypatch.setattr(action.subprocess, "run", lambda *args, **kwargs: pytest.fail("Candidate must never be downloaded"))
    assert action.main() == 23


@pytest.mark.parametrize("backend_override", [False, True])
def test_bootstrap_resolves_cache_lockfile_before_uv_setup(tmp_path, backend_override):
    """Execute the workflow bootstrap without uv, including a remote action checkout."""
    manifest = yaml.safe_load((ROOT / ".github/actions/rustinel-integration/action.yml").read_text())
    steps = manifest["runs"]["steps"]
    bootstrap = steps[0]
    assert bootstrap["id"] == "prepare"
    assert steps[1]["uses"].startswith("astral-sh/setup-uv@")
    cache_input = steps[1]["with"]["cache-dependency-glob"]
    assert cache_input == "${{ steps.prepare.outputs.cache-dependency-glob }}"

    backend = tmp_path / "downloaded action with spaces"
    action_dir = backend / ".github/actions/rustinel-integration"
    action_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / ".github/actions/rustinel-integration/run.py", action_dir / "run.py")
    chosen_backend = tmp_path / "separate backend" if backend_override else backend
    chosen_backend.mkdir(exist_ok=True)
    (chosen_backend / "uv.lock").write_text("lockfile fixture")
    output = tmp_path / "github-output"
    # Only Python is available in PATH. setup-uv has not installed uv yet.
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "python").symlink_to(sys.executable)
    result = subprocess.run(
        [shutil.which("bash"), "--noprofile", "--norc", "-e", "-c", bootstrap["run"]],
        env={
            **os.environ,
            "PATH": str(commands),
            "ACTION_DIR": f"{backend}/./.github/actions/../actions/rustinel-integration",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "INPUT_BACKEND_DIR": str(chosen_backend) if backend_override else "",
            "INPUT_RUSTINEL_ZIP": "",
            "INPUT_ASSET_DIR": "",
            "INPUT_OUTPUT_DIR": "",
            "INPUT_ARTIFACT_NAME": "",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    cache_path = values["cache-dependency-glob"]
    assert cache_path == (chosen_backend.resolve() / "uv.lock").as_posix()
    assert Path(cache_path).is_file()
    assert all(part not in (".", "..") for part in cache_path.split("/"))
