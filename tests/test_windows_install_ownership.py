"""Check ownership repair in the Windows installer delivered by the backend."""

import ast
import base64
import re
import subprocess

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_succeeds", [True, False])
async def test_rendered_windows_installer_repairs_ownership_or_aborts(client, monkeypatch, tmp_path, ownership_succeeds):
    response = await client.get("/device/install?os=windows")
    assert response.status_code == 200
    chunks = re.findall(r"\(echo\s+([A-Za-z0-9+/=]+)\)", response.text)
    source = base64.b64decode("".join(chunks)).decode("utf-8")
    module = {"__name__": "rendered_windows_installer"}
    exec(compile(source, "install-service.py", "exec"), module)  # noqa: S102 - trusted backend-generated installer
    commands = []

    def execute(command, *, check):
        commands.append(command)
        assert check is True
        if not ownership_succeeds:
            raise subprocess.CalledProcessError(5, command)

    monkeypatch.setattr(subprocess, "run", execute)
    directory = tmp_path / "Radegast"
    if ownership_succeeds:
        module["secure_installation_ownership"](directory)
    else:
        with pytest.raises(subprocess.CalledProcessError):
            module["secure_installation_ownership"](directory)
    assert commands == [["icacls", str(directory), "/setowner", "*S-1-5-32-544", "/T", "/Q"]]

    # Repair must actually be invoked before config delegation and service start.
    tree = ast.parse(source)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    statements = [ast.unparse(node) for node in main.body]
    repair = next(index for index, text in enumerate(statements) if text == "secure_installation_ownership(radegast_dir)")
    delegation = next(index for index, text in enumerate(statements) if "config_file" in text and "'/grant:r'" in text)
    startup = next(index for index, text in enumerate(statements) if "str(rustinel_service_exe), 'start'" in text)
    assert repair < delegation < startup
