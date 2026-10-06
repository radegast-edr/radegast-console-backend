"""Build a checkout wheel and prove the real installed agent came from it."""

import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import tomllib
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)


class WheelHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info("Source agent wheel: %s", format % args)


class SourceAgent:
    def __init__(self, source: Path, output: Path):
        self.source = source.resolve()
        self.output = output
        self.temporary = None
        self.server = None
        self.thread = None
        self.manifest = {}

    def start(self):
        self.output.mkdir(parents=True, exist_ok=True)
        project_text = (self.source / "pyproject.toml").read_text(encoding="utf-8")
        project = tomllib.loads(project_text)["project"]
        if project["name"] != "radegast-edr-agent":
            raise ValueError("agent-source must be a radegast-edr-agent checkout")
        files = {
            path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((self.source / "radegast_edr_agent").rglob("*.py"))
        }
        if not files:
            raise ValueError("agent-source contains no agent Python files")
        source_hash = hashlib.sha256((project_text + json.dumps(files, sort_keys=True)).encode()).hexdigest()
        version = project["version"].split("+", 1)[0] + "+integration." + source_hash[:16]
        self.temporary = tempfile.TemporaryDirectory(prefix="radegast-source-agent-")
        directory = Path(self.temporary.name)
        checkout = directory / "source"
        shutil.copytree(
            self.source,
            checkout,
            ignore=shutil.ignore_patterns(".git", ".venv", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", "test-output*"),
        )
        updated, replacements = re.subn(
            r'(?m)^version\s*=\s*["\'][^"\']+["\']\s*$',
            f'version = "{version}"',
            project_text,
            count=1,
        )
        if replacements != 1:
            raise ValueError("agent-source requires a static project version")
        (checkout / "pyproject.toml").write_text(updated, encoding="utf-8")
        wheels = directory / "wheels"
        with (self.output / "agent-build.log").open("w", encoding="utf-8") as build_log:
            subprocess.run(  # noqa: S603 - trusted source checkout, argv without a shell
                [shutil.which("uv") or "uv", "build", "--wheel", "--out-dir", str(wheels), str(checkout)],
                stdout=build_log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        built = list(wheels.glob("*.whl"))
        if len(built) != 1:
            raise RuntimeError("Expected exactly one source agent wheel")
        wheel = built[0]
        with zipfile.ZipFile(wheel) as archive:
            for filename, expected_hash in files.items():
                if hashlib.sha256(archive.read(filename)).hexdigest() != expected_hash:
                    raise RuntimeError(f"Wheel does not contain checkout content: {filename}")
        wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(WheelHandler, directory=str(wheels)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        wheel_url = f"http://127.0.0.1:{self.server.server_port}/{quote(wheel.name)}"
        self.manifest = {
            "version": version,
            "source_sha256": source_hash,
            "wheel_url": wheel_url,
            "wheel_sha256": wheel_hash,
            "files": files,
        }
        (self.output / "agent-source.json").write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        logger.info("Testing source agent %s from %s", version, self.source)

    @property
    def package_url(self):
        return self.manifest["wheel_url"] + "#sha256=" + self.manifest["wheel_sha256"]

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
        if self.temporary is not None:
            self.temporary.cleanup()

    def verify_installed(self, os_name):
        if os_name == "windows":
            python = (
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Radegast/agent/.tools/radegast-edr-agent/Scripts/python.exe"
            )
        else:
            home = Path("/opt/radegast/home" if os_name == "linux" else "/Library/Radegast/home")
            python = home / ".local/share/uv/tools/radegast-edr-agent/bin/python"
        result = subprocess.run(  # noqa: S603 - installed tool interpreter, argv without a shell
            [str(python), "-I", "-c", INSTALLED_PROVENANCE],
            input=json.dumps(self.manifest),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        (self.output / "agent-provenance.json").write_text(result.stdout or "{}", encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Installed agent is not the source wheel: {result.stdout}\n{result.stderr}")
        logger.info("Verified installed source wheel URL, checksum, version, and package contents")


# Run in the installed tool interpreter with -I so PYTHONPATH and user site packages
# cannot make a different checkout masquerade as the installed service package.
INSTALLED_PROVENANCE = """
import hashlib
import importlib.metadata
import json
import sys
import urllib.request

expected = json.load(sys.stdin)
distribution = importlib.metadata.distribution('radegast-edr-agent')
direct = json.loads(distribution.read_text('direct_url.json') or '{}')
archive = direct.get('archive_info', {})
digest = archive.get('hashes', {}).get('sha256') or archive.get('hash', '').removeprefix('sha256=')
errors = []
if distribution.version != expected['version']:
    errors.append('installed version does not match source version')
if direct.get('url') != expected['wheel_url']:
    errors.append('installed distribution did not come from the checksum-pinned source wheel URL')
else:
    with urllib.request.urlopen(expected['wheel_url'], timeout=10) as response:
        served_digest = hashlib.sha256(response.read()).hexdigest()
    if served_digest != expected['wheel_sha256'] or (digest and digest != expected['wheel_sha256']):
        errors.append('source wheel checksum does not match')
for filename, digest in expected['files'].items():
    path = distribution.locate_file(filename)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        errors.append('installed source content differs: ' + filename)
print(json.dumps({'version': distribution.version, 'direct_url': direct, 'errors': errors}, indent=2))
sys.exit(1 if errors else 0)
"""
