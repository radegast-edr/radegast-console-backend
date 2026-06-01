# Radegast EDR — Backend

Radegast EDR Backend is the management and orchestration server for the Radegast Endpoint Detection and Response (EDR) platform. Built with FastAPI and SQLAlchemy, it handles device authorisation, user configuration packs, age-encrypted log storage, alert status tracking, and key/session management.

## Features

1. **Device Management**: Creating and enrolling EDR agent devices, assigning them to Groups, and generating secure authorisation tokens.
2. **Configuration Packs**: Storing and distributing YAML/binary endpoint detection policies and versions.
3. **Encrypted Log Storage**: Accepting EDR agent telemetry/logs encrypted using `age`, verifying signatures, and managing user "seen" states for logged alerts.
4. **Team Collaboration**: Creating teams, managing device group permissions, and sending email notifications on critical events.
5. **Agent Distribution**: Serving the Rustinel eBPF sensor and its installation script to enrolled devices.

---

## Deployment (Podman / Docker)

The recommended way to run Radegast EDR in production is via the published container image.

### Quick start

```bash
# Pull and start with podman-compose (reads podman-compose.yaml)
podman-compose up -d

# Or with plain podman / docker
podman run -d \
  --name radegast-edr \
  -p 8000:8000 \
  -e RADEGAST_SECRET_KEY=<your-secret> \
  -e RADEGAST_BASE_URL=https://your.domain \
  -e RADEGAST_CORS_ORIGINS=https://your.domain \
  -v radegast_db:/app/data/db \
  -v radegast_uploads:/app/data/uploads \
  -v radegast_releases:/app/data/releases \
  docker.io/radegast-edr/console:latest
```

### Using podman-compose

Clone the repository and start all services with persistent named volumes:

```bash
git clone https://github.com/radegast-edr/radegast-backend.git
cd radegast-backend

# Edit the environment section in podman-compose.yaml first, then:
podman-compose up -d
```

Three named volumes are created automatically:

| Volume | Mount | Purpose |
|---|---|---|
| `radegast_database` | `/app/data/db` | SQLite database |
| `radegast_uploads` | `/app/data/uploads` | Uploaded configuration packs |
| `radegast_releases` | `/app/data/releases` | Rustinel agent release binaries |

The API is available at [http://localhost:8000](http://localhost:8000) and the interactive docs at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Local Development

### Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip`

### Installation

1. Install project dependencies:
   ```bash
   uv sync
   # Or using standard pip with a virtual environment:
   # python -m venv .venv && source .venv/bin/activate && pip install .
   ```

2. Install dev tools (test runner etc.):
   ```bash
   uv sync --dev
   # Or:
   # pip install .[dev]
   ```

### Running the Backend

Start the development server with hot-reload:
```bash
uv run uvicorn app.main:app --reload --port 8000
```

The server runs on [http://localhost:8000](http://localhost:8000). Interactive Swagger docs are available at [http://localhost:8000/docs](http://localhost:8000/docs).

### Running Tests

```bash
uv run pytest
```

---

## Configuration

All settings are controlled via environment variables prefixed with `RADEGAST_` (defined in [`app/config.py`](app/config.py)):

| Environment Variable | Default | Description |
|---|---|---|
| `RADEGAST_DATABASE_URL` | `sqlite+aiosqlite:///./radegast.db` | Async SQLAlchemy database URL |
| `RADEGAST_SECRET_KEY` | `change-me-in-production` | Secret key used for session signing — **must be changed in production** |
| `RADEGAST_CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated list of allowed CORS origins |
| `RADEGAST_BASE_URL` | `http://localhost:8000` | Public base URL of the API server (used in emails and install scripts) |
| `RADEGAST_UPLOAD_DIR` | `uploads/packs` | Directory where uploaded configuration packs are stored |
| `RADEGAST_RELEASES_DIR` | `agent/releases` | Directory containing Rustinel agent release binaries |
| `RADEGAST_SMTP_HOST` | `localhost` | Outgoing SMTP mail server |
| `RADEGAST_SMTP_PORT` | `587` | Outgoing SMTP server port |
| `RADEGAST_SMTP_USER` | _(empty)_ | SMTP authentication username |
| `RADEGAST_SMTP_PASSWORD` | _(empty)_ | SMTP authentication password |
| `RADEGAST_SMTP_FROM` | `noreply@radegast.local` | Sender address for outgoing emails |
| `RADEGAST_SESSION_COOKIE_NAME` | `radegast_session` | Name of the session cookie |
| `RADEGAST_SESSION_MAX_AGE` | `604800` | Session lifetime in seconds (default: 7 days) |
| `RADEGAST_TURNSTILE_SITE_KEY` | _(empty)_ | Cloudflare Turnstile Site Key for optional registration-protection |
| `RADEGAST_TURNSTILE_SECRET_KEY` | _(empty)_ | Cloudflare Turnstile Secret Key for verifying Turnstile responses |
| `RADEGAST_EMAIL_DEBOUNCE_SECONDS` | `180` | Email debounce limit in seconds before sending queued emails |
| `RADEGAST_ENABLE_EMAIL_WORKER` | `true` | Boolean flag to enable background email sending worker loop |
| `RADEGAST_WORKER_LOCK_PATH` | `/tmp/radegast-console.lock` | Path to the shared file lock used by single-thread workers |
| `RADEGAST_MFA_REQUIRED_LEVEL_ADMIN` | `hardware_token` | Required MFA level for Admin accounts (none, otp, hardware_token) |
| `RADEGAST_MFA_REQUIRED_LEVEL_MAINTAINER` | `none` | Required MFA level for Maintainer accounts (none, otp, hardware_token) |
| `RADEGAST_MFA_REQUIRED_LEVEL_USER` | `none` | Required MFA level for User accounts (none, otp, hardware_token) |
| `RADEGAST_WEBAUTHN_RP_ID` | _(empty)_ | Optional WebAuthn RP ID override. Set this to a shared parent domain (for example `radegast.app`) when API and Web UI run on different subdomains. |
| `RADEGAST_WEBAUTHN_ORIGINS` | _(empty)_ | Optional comma-separated extra WebAuthn origins allowed during verification (for example `https://console.radegast.app`). |
