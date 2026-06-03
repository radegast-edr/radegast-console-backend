# Agent Instructions for radegast-console-backend

## Repository Layout

- Backend code is in `app/`.
- Frontend code is in `web/`.
- Treat `web/` as a separate frontend project.

## Code style

- In Python, all imports are on the top of the file. stdlib first, 3rd party packages after a new line, local imports after than
- Cover all new features with new tests so that the test coverage does not decrease.

## Required Post-Task Validation

After completing each task, always run all validations in this order:

1. Rebuild frontend and fix all build/Svelte/a11y warnings:

```bash
cd web
npm run build
```

Make sure the build output contains no compile, Svelte, or accessibility warnings. Any warnings must be fixed before ending the task.

2. Run all frontend tests:

```bash
npm run test
```

3. Run all backend tests:

```bash
cd ..
uv run pytest
```

## Python Tooling Requirement

- Use `uv` for Python commands and dependency workflows.
- Prefer `uv run ...` for command execution (for example: `uv run pytest`, `uv run uvicorn ...`).
- Prefer `uv sync` / `uv sync --dev` for environment setup.

## Configuration Values

When adding any new configuration value:

1. Add it as a typed field in `app/config.py` under the `Settings` class.
2. The field name must follow the `radegast_`-prefix convention so it maps automatically to a `RADEGAST_*` environment variable.
3. Document the new `RADEGAST_*` variable in the configuration table in `README.md`.

## Database Migrations

If a database migration is needed (e.g. schema changes), the local SQLite database must be migrated, and the `DB_MIGRATION.md` file must be updated with the details of the migration in the format: `date - command - what was changed`.