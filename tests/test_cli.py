import os
from unittest.mock import ANY, patch

from app.cli import build_parser, cli


def test_build_parser():
    parser = build_parser()

    # Test 'run' subcommand
    args = parser.parse_args([
        "run",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--workers",
        "2",
        "--database-url",
        "sqlite+aiosqlite:///./test.db",
    ])
    assert args.command == "run"
    assert args.host == "127.0.0.1"
    assert args.port == 9000
    assert args.workers == 2
    assert args.database_url == "sqlite+aiosqlite:///./test.db"

    # Test boolean conversion optional options
    args_bool = parser.parse_args(["run", "--enable-email-worker"])
    assert args_bool.enable_email_worker is True

    args_no_bool = parser.parse_args(["run", "--no-enable-email-worker"])
    assert args_no_bool.enable_email_worker is False


@patch("uvicorn.run")
@patch("subprocess.run")
def test_cli_run_without_build_prompt_yes(mock_sub_run, mock_uvicorn_run):
    def side_effect_exists(self):
        if self.name == "web":
            return True
        return False

    # Mock Path.exists to return False for index.html checking so it prompts
    with (
        patch("pathlib.Path.exists", side_effect_exists),
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="yes"),
    ):
        with patch(
            "sys.argv",
            [
                "radegast-console",
                "run",
                "--port",
                "8500",
                "--database-url",
                "sqlite+aiosqlite:///./cli_test.db",
            ],
        ):
            cli()

            # Verify npm run build was called
            mock_sub_run.assert_any_call([ANY, "run", "build"], cwd=ANY, check=True)
            # Verify uvicorn was run
            mock_uvicorn_run.assert_called_once_with(
                "app.main:app", host="127.0.0.1", port=8500, workers=4
            )
            # Verify environment variables were set
            assert (
                os.environ.get("RADEGAST_DATABASE_URL")
                == "sqlite+aiosqlite:///./cli_test.db"
            )


@patch("uvicorn.run")
@patch("subprocess.run")
def test_cli_run_without_build_prompt_no(mock_sub_run, mock_uvicorn_run):
    def side_effect_exists(self):
        if self.name == "web":
            return True
        return False

    with (
        patch("pathlib.Path.exists", side_effect_exists),
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="no"),
    ):
        with patch("sys.argv", ["radegast-edr-console", "run", "--port", "8501"]):
            cli()

            # Verify npm run build was NOT called
            for call in mock_sub_run.call_args_list:
                assert "run" not in call[0] or "build" not in call[0]
            # Verify uvicorn was run
            mock_uvicorn_run.assert_called_once_with(
                "app.main:app", host="127.0.0.1", port=8501, workers=4
            )


@patch("subprocess.run")
@patch("shutil.which", return_value="/usr/bin/uv")
def test_cli_build(mock_which, mock_sub_run):
    with patch("sys.argv", ["radegast-edr-console", "build"]):
        cli()
        mock_sub_run.assert_called_once_with(["/usr/bin/uv", "build"], check=True)
