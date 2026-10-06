"""Coordinate the canonical integration scripts on native GitHub runners."""

# Workflow inputs are trusted; subprocess arguments are passed without a shell.
# ruff: noqa: S603, S607

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path


def prepare(env):
    action_dir = Path(env["ACTION_DIR"]).resolve()
    backend = Path(env.get("INPUT_BACKEND_DIR") or action_dir.parents[2]).resolve()
    os_name = {"Linux": "linux", "Darwin": "mac", "Windows": "windows"}[platform.system()]
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch = "m5" if os_name == "mac" else "arm64"
    elif machine in ("amd64", "x86_64"):
        arch = "amd64"
    else:
        raise ValueError(f"Unsupported architecture: {machine}")
    output = Path(env.get("INPUT_OUTPUT_DIR") or Path(env["RUNNER_TEMP"]) / f"test-output-{os_name}").resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = env.get("INPUT_RUSTINEL_ZIP", "")
    assets = env.get("INPUT_ASSET_DIR", "")
    if archive and assets:
        raise ValueError("Supply rustinel-zip or asset-dir, not both")
    assets = Path(assets or Path(env["RUNNER_TEMP"]) / f"rustinel-assets-{os_name}").resolve()
    archive = Path(archive).resolve() if archive else assets / f"upstream-{os_name}-{arch}.zip"
    return {
        "backend-dir": str(backend),
        "agent-source": str(Path(env["INPUT_AGENT_SOURCE"]).resolve()) if env.get("INPUT_AGENT_SOURCE") else "",
        "cache-dependency-glob": (backend / "uv.lock").as_posix(),
        "output-dir": str(output),
        "os": os_name,
        "arch": arch,
        "rustinel-zip": str(archive),
        "asset-dir": str(assets),
        "download": "false" if env.get("INPUT_RUSTINEL_ZIP") or env.get("INPUT_ASSET_DIR") else "true",
        "artifact-name": env.get("INPUT_ARTIFACT_NAME") or f"integration-test-diagnostics-{os_name}",
    }


def test_command(env):
    backend = Path(env["BACKEND_DIR"])
    command = [
        shutil.which("uv") or "uv",
        "run",
        "--project",
        str(backend),
        "python",
        str(backend / ".github/tests/integration_test.py"),
        "--backend-dir",
        str(backend),
        "--rustinel-zip",
        env["RUSTINEL_ZIP"],
        "--output-dir",
        env["OUTPUT_DIR"],
        "--os",
        env["TEST_OS"],
        "--arch",
        env["TEST_ARCH"],
        "--timeout",
        env["TEST_TIMEOUT"],
    ]
    if env.get("TEST_VERSION"):
        command += ["--version", env["TEST_VERSION"]]
    if env.get("AGENT_SOURCE"):
        command += ["--agent-source", env["AGENT_SOURCE"]]
    if env["TEST_OS"] != "windows":
        command = ["sudo", "env", f"PATH={env['PATH']}", "GITHUB_ACTIONS=true", *command]
    return command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "test", "report"))
    args = parser.parse_args()
    if args.mode == "prepare":
        values = prepare(os.environ)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    raise ValueError(f"Invalid multiline value for {key}")
                output.write(f"{key}={value}\n")
        return 0
    backend = Path(os.environ["BACKEND_DIR"])
    if args.mode == "test":
        if os.environ["DOWNLOAD"] == "true":
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(backend),
                    "python",
                    str(backend / ".github/tests/download_releases.py"),
                    "--release-kind",
                    "upstream",
                    "--os",
                    os.environ["TEST_OS"],
                    "--arch",
                    os.environ["TEST_ARCH"],
                    "--output-dir",
                    os.environ["ASSET_DIR"],
                ],
                check=True,
            )
        return subprocess.call(test_command(os.environ))
    # Only collected diagnostics are made readable, after the real test finishes.
    if os.environ["TEST_OS"] != "windows":
        subprocess.run(["sudo", "chmod", "-R", "a+rX", os.environ["OUTPUT_DIR"]], check=False)
    return subprocess.call(
        [
            "uv",
            "run",
            "--no-project",
            "--python",
            "3.12",
            "python",
            str(backend / ".github/tests/format_test_summary.py"),
            "--output-dir",
            os.environ["OUTPUT_DIR"],
            "--os",
            os.environ["TEST_OS"],
            "--arch",
            os.environ["TEST_ARCH"],
            "--exit-code",
            os.environ["TEST_EXIT_CODE"],
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
