# Agent Instructions for radegast-console-backend

## Repository Layout

- Backend code is in `app/`.
- Frontend code is in `web/`.
- Treat `web/` as a separate frontend project.

## Code style

- In Python, all imports are on the top of the file. stdlib first, 3rd party packages after a new line, local imports after than

## Required Post-Task Validation

After completing each task, always run both validations in this order:

1. Rebuild frontend:

```bash
cd web
npm run build
```

2. Run all backend tests:

```bash
cd ..
uv run pytest

- Python code 

## Python Tooling Requirement

- Use `uv` for Python commands and dependency workflows.
- Prefer `uv run ...` for command execution (for example: `uv run pytest`, `uv run uvicorn ...`).
- Prefer `uv sync` / `uv sync --dev` for environment setup.

## Configuration Values

When adding any new configuration value:

1. Add it as a typed field in `app/config.py` under the `Settings` class.
2. The field name must follow the `radegast_`-prefix convention so it maps automatically to a `RADEGAST_*` environment variable.
3. Document the new `RADEGAST_*` variable in the configuration table in `README.md`.