# Radegast EDR -- Backend

Radegast EDR is an Endpoint Detection and Response (EDR) platform designed for small teams and independent labs. The platform uses age end-to-end encryption (E2EE) to keep all log data private. The server stores only encrypted data that the server cannot read.

### Quickstarts

- **User Quickstart**: [First Steps with Radegast EDR](https://docs.radegast.app/quickstart#)
- **Backend Quickstart**: [Deployment and Quick Start](#quick-start)

The backend uses FastAPI and SQLAlchemy. It manages device authorization, configuration packs, encrypted log storage, alert statuses, and user sessions.

---

## Deployment (Podman or Docker)

To run Radegast EDR in production, use the published container image.

### Quick Start

```bash
# Start with podman-compose (reads podman-compose.yaml)
podman-compose up -d

# Or start with podman or docker directly
podman run -d \
  --name radegast-edr \
  -p 8000:8000 \
  -e RADEGAST_SECRET_KEY=<your-secret> \
  -e RADEGAST_BASE_URL=https://your.domain \
  -e RADEGAST_CORS_ORIGINS=https://your.domain \
  -v radegast_db:/app/data/db \
  -v radegast_uploads:/app/data/uploads \
  -v radegast_releases:/app/data/releases \
  docker.io/radegastedr/console:latest
```

### Using podman-compose

Clone the repository and start all services with persistent named volumes:

```bash
git clone https://github.com/radegast-edr/radegast-backend.git
cd radegast-backend

# Edit the environment section in podman-compose.yaml first, then run:
podman-compose up -d
```

The compose file creates three named volumes automatically:

| Volume | Mount Path | Description |
|---|---|---|
| `radegast_database` | `/app/data/db` | SQLite database |
| `radegast_uploads` | `/app/data/uploads` | Uploaded detection packs |
| `radegast_releases` | `/app/data/releases` | Rustinel agent release binaries |

The API listens on [http://localhost:8000](http://localhost:8000). Interactive Swagger documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

### Using MySQL as Database

The application uses SQLite by default. To use MySQL or MariaDB:

1. Create your MySQL database.
2. Start the container with `RADEGAST_DATABASE_URL` using the `mysql+aiomysql://` driver:
   ```bash
   docker run -d \
     --name radegast-edr \
     -p 8000:8000 \
     -e RADEGAST_SECRET_KEY=<your-secret> \
     -e RADEGAST_DATABASE_URL="mysql+aiomysql://user:password@mysql-host:3306/db_name" \
     -e RADEGAST_BASE_URL=https://your.domain \
     -e RADEGAST_CORS_ORIGINS=https://your.domain \
     -v radegast_uploads:/app/data/uploads \
     -v radegast_releases:/app/data/releases \
     docker.io/radegastedr/console:latest
   ```

The `aiomysql` driver parses query parameters such as `?ssl_verify_cert=false`, `?ssl_ca=/path/to/ca.pem`, and `?ssl_cert=...` to configure TLS connections.

To copy an existing SQLite database (for example, `radegast.db`) to MySQL, run the migration script:

```bash
# Run migration (applies Alembic migrations to MySQL and copies all data)
uv run python migrate-to-mysql.py \
  --sqlite-path radegast.db \
  --mysql-url "mysql+aiomysql://user:password@mysql-host:3306/db_name?ssl_ca=cert.pem"
```

The script runs Alembic migrations on MySQL, normalizes datetime formats, batches transactions, and verifies row counts across all tables.

---

## Local Development

### Prerequisites

- Python 3.11 or later
- [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip`

### Installation

1. Install project dependencies:
   ```bash
   uv sync
   # Or install with MySQL support:
   # uv sync --group mysql
   
   # Or using standard pip with a virtual environment:
   # python -m venv .venv && source .venv/bin/activate && pip install .
   # Or with MySQL support:
   # pip install .[mysql]
   ```

2. Install development tools:
   ```bash
   uv sync --dev
   # Or:
   # pip install .[dev]
   ```

### Start the Backend

Start the development server with automatic reload:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

The server listens on [http://localhost:8000](http://localhost:8000). Interactive Swagger documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

### Run with the CLI

You can start the application using the CLI:

```bash
uv run radegast-console run --host=127.0.0.1 --port=8000 --workers=4
```

Alternatively, install the tool globally with `uv`:

```bash
uv tool install radegast-edr-console
```

After installation, start the console:

```bash
radegast-console run --host=127.0.0.1 --port=8000 --workers=4
```

Run `radegast-console run --help` to view all available CLI parameters.

### Run Tests

```bash
uv run pytest
```

---

## Configuration

Environment variables with the `RADEGAST_` prefix control all configuration settings (defined in [`app/config.py`](app/config.py)):

| Environment Variable | Required | Default | Description |
|---|---|---|---|
| `RADEGAST_ENVIRONMENT` | N | `prod` | Deployment environment (`dev` or `prod`). If `dev`, skips secret key warning. |
| `RADEGAST_SECRET_KEY` | Y | `change-me-in-production` | Secret key used for session signing. Must be changed in production. |
| `RADEGAST_DATABASE_URL` | N | `sqlite+aiosqlite:///./radegast.db` | Async SQLAlchemy database connection URL. |
| `RADEGAST_CORS_ORIGINS` | N | `http://localhost:5173,...` | Comma-separated list of allowed CORS origins. |
| `RADEGAST_BASE_URL` | N | `http://localhost:8000` | Public base URL of the API server. |
| `RADEGAST_UPLOAD_DIR` | N | `uploads/packs` | Directory where uploaded detection packs are stored. |
| `RADEGAST_RELEASES_DIR` | N | `agent/releases` | Directory containing Rustinel agent release binaries. |
| `RADEGAST_SMTP_HOST` | N | _(none)_ | Outgoing SMTP mail server. If unset, emails are printed to stdout. |
| `RADEGAST_SMTP_PORT` | N | `587` | Outgoing SMTP server port. |
| `RADEGAST_SMTP_USER` | N | _(empty)_ | SMTP authentication username. |
| `RADEGAST_SMTP_PASSWORD` | N | _(empty)_ | SMTP authentication password. |
| `RADEGAST_SMTP_FROM` | N | `noreply@radegast.local` | Sender email address for outgoing messages. |
| `RADEGAST_SMTP_STARTTLS` | N | `true` | Enables or disables SMTP STARTTLS. |
| `RADEGAST_SESSION_COOKIE_NAME` | N | `radegast_session` | Name of the session cookie. |
| `RADEGAST_SESSION_MAX_AGE` | N | `604800` | Session lifetime in seconds (default: 7 days). |
| `RADEGAST_WEB_UI_URL` | N | _(empty)_ | URL of the web UI if hosted on a separate domain. |
| `RADEGAST_TURNSTILE_SITE_KEY` | N | _(empty)_ | Cloudflare Turnstile Site Key for registration protection. |
| `RADEGAST_TURNSTILE_SECRET_KEY` | N | _(empty)_ | Cloudflare Turnstile Secret Key for verifying Turnstile responses. |
| `RADEGAST_REGISTRATION_MESSAGE` | N | _(empty)_ | Optional message displayed on the registration page. |
| `RADEGAST_EMAIL_DEBOUNCE_SECONDS` | N | `180` | Delay in seconds before sending queued emails. |
| `RADEGAST_EMAIL_BULK_INTERVALS` | N | `3,3,6,16,37,62,122,193` | Comma-separated list of bulk debounce intervals in minutes. |
| `RADEGAST_EMAIL_BULK_RESET_HOURS` | N | `24` | Hours after which the bulk notification sequence resets. |
| `RADEGAST_ENABLE_EMAIL_WORKER` | N | `true` | Enables or disables the background email worker. |
| `RADEGAST_ENABLE_SPACE_USAGE_WORKER` | N | `true` | Enables or disables the space usage computation worker. |
| `RADEGAST_SPACE_USAGE_INTERVAL_MINUTES` | N | `15` | Minutes between periodic storage calculations. |
| `RADEGAST_ENABLE_DEVICE_DELETION_WORKER` | N | `true` | Enables or disables the deleted device cleanup worker. |
| `RADEGAST_DEVICE_DELETION_INTERVAL_MINUTES` | N | `15` | Minutes between device cleanup runs. |
| `RADEGAST_WORKER_LOCK_PATH` | N | `/tmp/radegast-console.lock` | Path to the file lock used for single-instance worker execution. |
| `RADEGAST_MFA_REQUIRED_LEVEL_ADMIN` | N | `hardware_token` | Required MFA level for Admin accounts (`none`, `otp`, `hardware_token`). |
| `RADEGAST_MFA_REQUIRED_LEVEL_MAINTAINER` | N | `none` | Required MFA level for Maintainer accounts (`none`, `otp`, `hardware_token`). |
| `RADEGAST_MFA_REQUIRED_LEVEL_USER` | N | `none` | Required MFA level for User accounts (`none`, `otp`, `hardware_token`). |
| `RADEGAST_WEBAUTHN_RP_ID` | N | _(empty)_ | WebAuthn RP ID override for cross-subdomain deployments. |
| `RADEGAST_WEBAUTHN_ORIGINS` | N | _(empty)_ | Comma-separated list of allowed WebAuthn origins. |
| `RADEGAST_PACK_MAX_SIZE_MB` | N | _(none)_ | Maximum detection pack zip size in megabytes. |
| `RADEGAST_PACK_MAX_SIZE_MB_USER` | N | _(none)_ | Maximum pack zip size for regular users in megabytes. |
| `RADEGAST_PACK_MAX_SIZE_MB_MAINTAINER` | N | _(none)_ | Maximum pack zip size for maintainer accounts in megabytes. |
| `RADEGAST_PACK_MAX_SIZE_MB_ADMIN` | N | _(none)_ | Maximum pack zip size for admin accounts in megabytes. |
| `RADEGAST_AGENT_PACKAGE` | N | `radegast-edr-agent` | Agent package name or path for installation via `uv`. |
| `RADEGAST_ACCOUNT_DELETION_GRACE_DAYS` | N | `14` | Grace period in days before account deletion completes. |

---

## AI Usage Disclosure

The authors used GenAI tools to assist in writing code for this repository. Every commit is reviewed and verified by human maintainers.
