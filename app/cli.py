import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
import uvicorn

from app.config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Radegast Console CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the Radegast Console server")

    # Add standard run parameters
    run_parser.add_argument("--host", default="127.0.0.1", help="Bind socket to this host")
    run_parser.add_argument("--port", type=int, default=8000, help="Bind socket to this port")
    run_parser.add_argument("--workers", type=int, default=4, help="Number of worker processes")

    # Dynamically add all Settings fields as arguments to the run parser
    for name, field in Settings.model_fields.items():
        cli_option = name.replace("_", "-")

        # Determine the type, choices, and defaults
        annotation = field.annotation
        # We need to resolve Optional / Union types to their main type
        from typing import get_origin, get_args, Union
        import types

        origin = get_origin(annotation)
        if origin in (Union, getattr(types, "UnionType", None)):
            args = [a for a in get_args(annotation) if a is not type(None)]
            if args:
                annotation = args[0]
                origin = get_origin(annotation)

        # Check if type is boolean
        if annotation is bool:
            # Add boolean options
            run_parser.add_argument(
                f"--{cli_option}",
                action=argparse.BooleanOptionalAction,
                default=None,
                help=f"Override config {name}",
            )
        else:
            # Determine choices from Literal
            from typing import Literal
            choices = None
            if origin is Literal:
                choices = get_args(annotation)

            # Determine expected type
            arg_type = str
            if annotation is int:
                arg_type = int
            elif annotation is float:
                arg_type = float

            run_parser.add_argument(
                f"--{cli_option}",
                type=arg_type,
                choices=choices,
                default=None,
                help=f"Override config {name}",
            )

    # Build command
    subparsers.add_parser("build", help="Build the PyPI package (automatically builds frontend)")

    return parser


def cli():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        # 1. Check if the frontend is built.
        root_dir = Path(__file__).parent.parent
        web_build_pkg = root_dir / "app" / "web_build"
        web_build_git = root_dir / "web" / "build"

        has_web_pkg = web_build_pkg.exists() and (web_build_pkg / "index.html").exists()
        has_web_git = web_build_git.exists() and (web_build_git / "index.html").exists()

        if not has_web_pkg and not has_web_git:
            if sys.stdin.isatty():
                try:
                    response = (
                        input(
                            "Frontend web build not found. Would you like to build the frontend now? [y/N]: "
                        )
                        .strip()
                        .lower()
                    )
                except (KeyboardInterrupt, EOFError):
                    print("\nBuild aborted.")
                    sys.exit(1)
                if response in ("y", "yes"):
                    print("Building frontend...")
                    web_dir = root_dir / "web"
                    if not web_dir.exists():
                        print("Error: 'web/' directory not found. Cannot build frontend.")
                        sys.exit(1)
                    try:
                        # Build the web project
                        subprocess.run(["npm", "run", "build"], cwd=web_dir, check=True)
                        print("Frontend built successfully.")
                    except subprocess.CalledProcessError as e:
                        print(f"Error: npm build failed: {e}")
                        sys.exit(1)
                else:
                    print("Proceeding without building frontend.")
            else:
                print("Warning: Frontend web build not found (and stdin is not a TTY). Proceeding without UI.")

        # 2. Process configuration overrides.
        from app.config import settings

        for name, field in Settings.model_fields.items():
            val = getattr(args, name)
            if val is not None:
                # Update settings in-place
                setattr(settings, name, val)
                # Set environment variable so any uvicorn workers inherit it
                env_key = f"RADEGAST_{name.upper()}"
                if isinstance(val, bool):
                    os.environ[env_key] = str(val).lower()
                else:
                    os.environ[env_key] = str(val)

        # 3. Start uvicorn
        print(f"Starting server on {args.host}:{args.port} with {args.workers} workers...")
        uvicorn.run("app.main:app", host=args.host, port=args.port, workers=args.workers)

    elif args.command == "build":
        print("Building PyPI package (frontend build will be executed automatically by Hatch)...")
        try:
            if shutil.which("uv"):
                subprocess.run(["uv", "build"], check=True)
            elif shutil.which("hatch"):
                subprocess.run(["hatch", "build"], check=True)
            else:
                subprocess.run([sys.executable, "-m", "build"], check=True)
            print("Package built successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Error: package build failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    cli()
