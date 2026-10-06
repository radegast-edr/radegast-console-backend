#!/usr/bin/env python3
"""Download the newest Rustinel release zip files from the public Backend API.

Queries https://console-api.radegast.app/api/v1/public/releases/ to dynamically find
the latest release per platform and downloads each zip into test-assets/ with metadata preserving the release version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import zipfile
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("download-releases")

PUBLIC_API_URL = "https://console-api.radegast.app"
HEADERS = {"User-Agent": "radegast-asset-downloader/1.0"}


def parse_release_version(ver_str: str) -> tuple[int, int, int, int]:
    """Parse version string into a comparable tuple (major, minor, patch, revision)."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:r(\d+))?$", ver_str.strip())
    if match:
        major, minor, patch, rev = match.groups()
        return (int(major), int(minor), int(patch), int(rev or 0))
    # Fallback: extract any digits
    digits = [int(d) for d in re.findall(r"\d+", ver_str)]
    while len(digits) < 4:
        digits.append(0)
    return tuple(digits[:4])  # type: ignore[return-value]


def fetch_all_releases(api_base: str = PUBLIC_API_URL) -> list[dict]:
    """Fetch the release manifest list from the public API."""
    url = f"{api_base.rstrip('/')}/api/v1/public/releases/"
    logger.info("Fetching releases list from %s...", url)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()


def get_newest_releases_by_platform(releases: list[dict], release_kind: str = "all") -> dict[tuple[str, str], dict]:
    """Group releases by (os, arch) and select the highest version for each."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in releases:
        is_patched = bool(re.fullmatch(r"\d+\.\d+\.\d+r\d+", r["version"]))
        if release_kind == "upstream" and is_patched:
            continue
        if release_kind == "patched" and not is_patched:
            continue
        key = (r["os"], r["arch"])
        grouped.setdefault(key, []).append(r)

    newest: dict[tuple[str, str], dict] = {}
    for key, items in grouped.items():
        items.sort(key=lambda x: parse_release_version(x["version"]))
        newest[key] = items[-1]
    return newest


def download_release(
    api_base: str,
    version: str,
    os_name: str,
    arch: str,
    dest_path: Path,
) -> Path:
    """Download a specific release zip from the public API."""
    url = f"{api_base.rstrip('/')}/api/v1/public/releases/{version}/{os_name}/{arch}/download"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest_path.with_suffix(".tmp")

    logger.info("Downloading %s/%s v%s from %s...", os_name, arch, version, url)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        with client.stream("GET", url, headers=HEADERS) as resp:
            resp.raise_for_status()
            with open(temp_dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)

    # Validate zip integrity
    if not zipfile.is_zipfile(temp_dest):
        temp_dest.unlink(missing_ok=True)
        raise ValueError(f"Downloaded file from {url} is not a valid zip archive.")

    with zipfile.ZipFile(temp_dest, "r") as zf:
        bad_file = zf.testzip()
        if bad_file:
            temp_dest.unlink(missing_ok=True)
            raise ValueError(f"Zip corrupted; first bad file: {bad_file}")
        namelist = zf.namelist()

    temp_dest.replace(dest_path)
    logger.info(
        "Successfully saved %s (size: %d bytes, files: %s)",
        dest_path,
        dest_path.stat().st_size,
        namelist[:5],
    )
    return dest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Download newest Rustinel releases from public API.")
    parser.add_argument(
        "--api-url",
        default=PUBLIC_API_URL,
        help="Base URL of backend public API",
    )

    def find_default_backend_dir() -> Path:
        for p in [Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1], Path.cwd()]:
            if (p / "app" / "main.py").exists():
                return p
        return Path.cwd()

    parser.add_argument(
        "--backend-dir",
        type=Path,
        default=find_default_backend_dir(),
        help="Path to radegast-console-backend directory",
    )
    parser.add_argument(
        "--os",
        dest="os_name",
        type=str,
        default=None,
        help="Filter to specific OS (linux, windows, mac)",
    )
    parser.add_argument(
        "--arch",
        type=str,
        default=None,
        help="Filter to specific arch (amd64, arm64, m5)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional destination directory for test-assets",
    )
    parser.add_argument(
        "--release-kind",
        choices=("upstream", "patched", "all"),
        default="all",
        help="Artifact kind: all tests the newest published release; upstream selects upstream-sync artifact kinds",
    )
    args = parser.parse_args()

    backend_dir = args.backend_dir
    test_assets_dir = args.output_dir or (backend_dir / "test-assets")

    releases = fetch_all_releases(args.api_url)
    newest_by_platform = get_newest_releases_by_platform(releases, args.release_kind)

    # Filter if requested
    selected: dict[tuple[str, str], dict] = {}
    for (os_name, arch), rel in newest_by_platform.items():
        if args.os_name and os_name != args.os_name:
            continue
        if args.arch and arch != args.arch:
            continue
        selected[(os_name, arch)] = rel

    if not selected:
        logger.error("No releases matching criteria (os=%s, arch=%s)", args.os_name, args.arch)
        return 1

    logger.info("Found %d platforms to download:", len(selected))
    for (os_name, arch), rel in selected.items():
        logger.info("  - %s/%s -> v%s (%d bytes)", os_name, arch, rel["version"], rel.get("size_bytes", 0))

    for (os_name, arch), rel in selected.items():
        version = rel["version"]

        # 1. Download to test-assets/upstream-{os}-{arch}.zip
        test_asset_path = test_assets_dir / f"upstream-{os_name}-{arch}.zip"
        download_release(args.api_url, version, os_name, arch, test_asset_path)

        metadata = {
            "version": version,
            "os": os_name,
            "arch": arch,
            "sha256": hashlib.sha256(test_asset_path.read_bytes()).hexdigest(),
        }
        test_asset_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    logger.info("All requested platform releases downloaded successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
