#!/usr/bin/env python3
"""Report integration results with bounded, redacted service diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path


def redact_credentials(content: str) -> str:
    content = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[REDACTED]", content)
    return re.sub(
        r"""(?ix)
        ((?:[a-z_]*(?:token|password|secret)[a-z_]*|authorization)
        \s*["']?\s*(?:=>|:|=)\s*["']?)
        [^\s"',<>]+""",
        r"\1[REDACTED]",
        content,
    )


def append_collapsible_log(sf, title: str, log_file: Path, max_lines: int = 100, console: bool = False):
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        lines = redact_credentials(content).splitlines()
        content = "\n".join(lines)
        if len(lines) > max_lines:
            content = f"... (last {max_lines} lines)\n" + "\n".join(lines[-max_lines:])
        if not content.strip():
            content = f"{log_file.name} is empty."
    except OSError as exc:
        content = f"Cannot read {log_file.name}: {exc}"
    if sf is not None:
        sf.write(f"<details>\n<summary>{title}</summary>\n\n```text\n{content}\n```\n</details>\n\n")
    if console:
        print(f"::group::{title}", flush=True)
        print(content, flush=True)
        print("::endgroup::", flush=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Report real-device integration results")
    parser.add_argument("--output-dir")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--os", default="unknown")
    parser.add_argument("--arch", default="unknown")
    parser.add_argument("--zip", nargs=2, metavar=("ZIP_OUT", "BIN_IN"))
    args = parser.parse_args()
    if args.zip:
        zip_out, bin_in = args.zip
        with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(bin_in, arcname="rustinel.exe" if bin_in.endswith(".exe") else "rustinel")
        return 0
    if not args.output_dir:
        parser.error("--output-dir is required when not using --zip")

    out_dir = Path(args.output_dir)
    data = {}
    result_error = None
    try:
        data = json.loads((out_dir / "test_result.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("result must be a JSON object")
    except (OSError, ValueError) as exc:
        data = {}
        result_error = f"No valid test result: {exc}. Check setup steps and the runner diagnostics."
    exit_code = args.exit_code
    if data.get("status") != "PASS" and exit_code == 0:
        exit_code = 1
    platform = f"{data.get('os', args.os)}/{data.get('arch', args.arch)}"
    version = data.get("detected_version", "unknown")
    status = "PASSED" if exit_code == 0 else "FAILED"
    stage = data.get("failure_stage")
    error = data.get("error") or result_error
    print(f"Integration Test Report ({platform})", flush=True)
    print(f"Status: {status} (exit code {exit_code})", flush=True)
    print(f"Version: {version}", flush=True)
    if stage:
        print(f"Failed stage: {stage}", flush=True)
    if error:
        print(f"Reason: {error}", flush=True)
    print("Full logs and alert payloads are available in the diagnostics artifact.", flush=True)

    sf = None
    summary_file = args.summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    try:
        if summary_file:
            try:
                sf = open(summary_file, "a", encoding="utf-8")
            except OSError as exc:
                print(f"Cannot open step summary: {exc}", file=sys.stderr)
        if sf is not None:
            sf.write(f"\n### Integration Test ({platform})\n\n**{status}** - release `{version}`\n\n")
            if stage:
                sf.write(f"Failed stage: `{stage}`\n\n")
            if error:
                sf.write(f"Reason: {error}\n\n")
            if exit_code == 0:
                sf.write("Official installation, running services, and native healthcheck pipeline verified.\n\n")
            sf.write("Full logs and alert payloads are in the diagnostics artifact.\n\n")
        for filename in (
            "runner.log",
            "failure.log",
            "migrations.log",
            "installer.log",
            "backend.log",
            "agent-build.log",
            "filesystem_permissions.txt",
            "process_identity.txt",
        ):
            if filename in ("runner.log", "installer.log") or (out_dir / filename).is_file():
                append_collapsible_log(sf, filename, out_dir / filename, console=exit_code != 0)
        if exit_code != 0:
            for log_file in sorted(out_dir.rglob("*.log*")):
                if log_file.is_file() and log_file.name.startswith(("rustinel", "radegast-rustinel", "radegast-agent", "radegast-updater")):
                    append_collapsible_log(sf, log_file.relative_to(out_dir).as_posix(), log_file, console=True)
    finally:
        if sf is not None:
            sf.close()
    # The test step owns failure status; reporting must not mask its diagnostics.
    return 0


if __name__ == "__main__":
    sys.exit(main())
