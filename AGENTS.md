# Agent Instructions for radegast-console-backend

## Repository Layout

- Backend code is in `app/`.
- Frontend code is in `web/`.
- Treat `web/` as a separate frontend project.

## Code style

- In Python, all imports are on the top of the file. stdlib first, 3rd party packages after a new line, local imports after than
- Cover all new features with new tests so that the test coverage does not decrease.

## API Type Generation

The frontend TypeScript types in `web/src/lib/openapi.d.ts` are auto-generated from the backend's OpenAPI schema. Whenever you modify the backend API (add/change/remove endpoints or Pydantic models), regenerate the types:

1. Make sure the backend is running locally:

```bash
uv run uvicorn app.main:app --port 8000
```

2. In a separate terminal, regenerate the types:

```bash
uv run gen-openapi.py
cd web
npm run generate
```

3. Rebuild the frontend to verify no type errors were introduced:

```bash
npm run build
```

The `generate` script fetches `http://localhost:8000/openapi.json` and writes `src/lib/openapi.d.ts`. Do **not** edit `openapi.d.ts` by hand — it will be overwritten on the next run.

## Required Post-Task Validation

After completing each task, always run all validations in this order:

1. **Run Ruff formatting and linting** on backend code:

```bash
uv run ruff check app/ --fix
uv run ruff format app/
```

Fix any formatting or linting issues reported by Ruff.

2. Rebuild frontend and fix all build/Svelte/a11y warnings:

```bash
cd web
npm run build
```

Make sure the build output contains no compile, Svelte, or accessibility warnings. Any warnings must be fixed before ending the task.

3. **Run ESLint checking** on frontend code:

```bash
cd web
npm run lint
```

Fix any style or linting issues reported by ESLint.

4. Run all frontend tests:

```bash
npm run test
```

5. Run all backend tests:

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

We use **Alembic** to manage versioned database migrations. 

When a database schema change is made:
1. Generate a new versioned migration script:
   ```bash
   uv run alembic revision --autogenerate -m "description_of_change"
   ```
2. **Ensure migrations are safe and conditional**:
   To prevent failures on pre-existing databases or fresh startup environments, check if tables, columns, or indexes exist before attempting to create/modify them in your `upgrade()` and `downgrade()` routines. You can get an inspector using `sa.inspect(op.get_bind())` and write conditional logic.
3. Test the migration locally on both a fresh database and a replica of the current database:
   ```bash
   uv run python apply-migrations.py
   ```
4. All migration histories are tracked natively by Alembic under `migrations/versions/` (which is the sole source of truth for database versions).


## Documentation

The project documentation is built using Sphinx with MyST Parser for Markdown support and hosted on ReadTheDocs.

### Updating Documentation

When modifying documentation files in `docs/` or `README.md`:

1. **Local rebuild** to verify changes:

```bash
cd docs
uv pip install -r requirements.txt
LC_ALL=C uv run sphinx-build -b html . _build/html
```

2. **Check for warnings** - The build should complete with no warnings.

3. **Verify links** - Open `_build/html/index.html` in a browser to test navigation.

4. **Update README.md** - If documentation changes affect the main README, update it as well.

### Documentation Files

- User guides: `docs/user-guides/*.md` (Markdown format, parsed by MyST)
- Configuration: `docs/conf.py` (Sphinx settings)
- Main index: `docs/index.rst` (Table of contents)
- ReadTheDocs config: `.readthedocs.yaml`

**IMPORTANT**: Always rebuild and verify documentation before committing changes.

## UI Styling & Theme Guidelines

- **Theme Compatibility**: Do not hardcode light or dark background/text classes (e.g., `bg-light`, `text-dark`) on dynamic/interactive components. Use theme-aware Bootstrap classes instead (e.g., `bg-body-secondary`, `text-body`).
- **Global Modal Component**: Always use the predefined global `Modal` component (`import Modal from '$lib/components/Modal.svelte'`) for rendering dialog popups. Do not write custom inline modal layouts, overlay backdrops, or manual positioning.
- **Interactive Component State**: Do not rely on native Bootstrap JavaScript components (like dropdown toggle library handlers) inside Svelte templates. Bootstrap's bundle JavaScript is not imported. Always manage the open/collapsed state of dropdowns, popups, and accordions reactively using Svelte state variables (e.g., `{dropdownOpen ? 'show' : ''}`).
- **Sharp Design System**: Respect the application's sharp design token rules in `app.html` (e.g., `border-radius: 3px !important` is applied globally). Do not specify arbitrary inline curves or border radius styles (like `border-radius: 12px;` or `border-radius: 16px;`) on cards, tables, modals, or buttons.