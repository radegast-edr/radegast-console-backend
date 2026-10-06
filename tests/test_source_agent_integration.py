"""Build and install a real source wheel, then reject altered or index-installed agents."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".github/tests"
spec = importlib.util.spec_from_file_location("source_agent_tooling", SCRIPTS / "source_agent.py")
source_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source_agent)


@pytest.fixture(scope="module")
def installed_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("source wheel")
    checkout = root / "checkout"
    package = checkout / "radegast_edr_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 'checkout-source'\n")
    pyproject = """[project]
name = "radegast-edr-agent"
version = "1.0.1"
requires-python = ">=3.11"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["radegast_edr_agent"]
"""
    (checkout / "pyproject.toml").write_text(pyproject)
    candidate = source_agent.SourceAgent(checkout, root / "diagnostics")
    try:
        candidate.start()
        uv = shutil.which("uv")
        environment = root / "installed"
        subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], check=True, capture_output=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([uv, "pip", "install", "--python", str(python), "--no-deps", candidate.package_url], check=True, capture_output=True)
        assert (checkout / "pyproject.toml").read_text() == pyproject
        yield candidate, python
    finally:
        candidate.stop()


def inspect_installed(candidate, python, manifest=None):
    return subprocess.run(
        [str(python), "-I", "-c", source_agent.INSTALLED_PROVENANCE],
        input=json.dumps(manifest or candidate.manifest),
        capture_output=True,
        text=True,
        check=False,
    )


def test_source_wheel_download_and_installed_provenance(installed_source):
    candidate, python = installed_source
    response = httpx.get(candidate.manifest["wheel_url"])
    response.raise_for_status()
    assert hashlib.sha256(response.content).hexdigest() == candidate.manifest["wheel_sha256"]
    result = inspect_installed(candidate, python)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["errors"] == []


@pytest.mark.parametrize(
    "field,value", [("version", "1.0.1"), ("wheel_url", "https://pypi.org/not-the-checkout.whl"), ("wheel_sha256", "0" * 64)]
)
def test_source_provenance_rejects_other_versions_and_origins(installed_source, field, value):
    candidate, python = installed_source
    result = inspect_installed(candidate, python, {**candidate.manifest, field: value})
    assert result.returncode == 1
    assert json.loads(result.stdout)["errors"]


def test_source_provenance_checks_installed_python_contents(installed_source):
    candidate, python = installed_source
    manifest = {**candidate.manifest, "files": {"radegast_edr_agent/__init__.py": "0" * 64}}
    result = inspect_installed(candidate, python, manifest)
    assert result.returncode == 1
    assert "installed source content differs" in result.stdout


def test_source_provenance_rejects_missing_installation_origin(installed_source):
    candidate, python = installed_source
    location = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import importlib.metadata as m; d=m.distribution('radegast-edr-agent'); "
            "print(next(d.locate_file(f) for f in d.files if str(f).endswith('/direct_url.json')))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    direct_url = Path(location.stdout.strip())
    original = direct_url.read_bytes()
    try:
        direct_url.unlink()
        result = inspect_installed(candidate, python)
        assert result.returncode == 1
        assert "did not come from" in result.stdout
    finally:
        direct_url.write_bytes(original)


def test_pypi_service_version_cannot_satisfy_source_healthcheck(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    runner_spec = importlib.util.spec_from_file_location("source_healthcheck_runner", SCRIPTS / "integration_test.py")
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"healthy": True, "agent_version": "1.0.1"}))
    client_type = httpx.Client
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(RuntimeError, match="expected source agent"):
        runner.verify_healthcheck_alert("http://backend/api/v1", "session", 1, expected_agent_version="1.0.1+integration.checkout")
