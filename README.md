# Radegast EDR — Backend

Radegast EDR is a lightweight, privacy-focused Endpoint Detection and Response platform **perfect for smaller teams, home labbers, and families**. With complete end-to-end encryption (E2EE) using age encryption, your log data remains private and secure — even from the server itself. **No custom infrastructure is required**: the built-in SQLite database and self-contained deployment make it easy to get started without complex setup. You don't need to host any custom infrastructure if you don't want to.

Built with FastAPI and SQLAlchemy, the backend handles device authorization, user configuration packs, encrypted log storage, alert status tracking, and key/session management.

## Features

- **Device Management**: Create and enroll EDR agent devices, assign them to groups, and generate secure authorization tokens
- **Configuration Packs**: Store and distribute YAML/binary endpoint detection policies and versions
- **End-to-End Encrypted Log Storage**: All logs are encrypted on the device using `age` before transmission; the server stores only encrypted data it cannot read
- **Team Collaboration**: Create teams, manage device group permissions, and receive email notifications for critical events
- **Zero-Trust Architecture**: All data is encrypted client-side; the server never has access to your private keys or decrypted log contents
- **Self-Contained Deployment**: Built-in SQLite database means no external database server required
- **Agent Distribution**: Serve the Rustinel eBPF sensor and provide single-command installation for Linux and Windows

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
  docker.io/radegastedr/console:latest
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

### Running with the CLI

You can run the application directly via the CLI interface. In development, use:

```bash
uv run radegast-console run --host=127.0.0.1 --port=8000 --workers=4
```

Alternatively, you can install the tool globally using `uv`:

```bash
uv tool install radegast-edr-console
```

Once installed, start the console using:

```bash
radegast-console run --host=127.0.0.1 --port=8000 --workers=4
```

You can pass any configuration variable (e.g., `--database-url`, `--enable-email-worker`) to override defaults. Run `radegast-console run --help` to see a full list of options.

### Running Tests

```bash
uv run pytest
```

---

## Configuration

All settings are controlled via environment variables prefixed with `RADEGAST_` (defined in [`app/config.py`](app/config.py)):

| Environment Variable                     | Required | Default                             | Description                                                                                                                                        |
|------------------------------------------|----------|-------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| `RADEGAST_ENVIRONMENT`                   | N        | `prod`                              | The deployment environment. Valid values: `dev`, `prod`. If `dev`, skips default secret key warning.                                               |
| `RADEGAST_SECRET_KEY`                    | Y        | `change-me-in-production`           | Secret key used for session signing — **must be changed in production**                                                                            |
| `RADEGAST_DATABASE_URL`                  | N        | `sqlite+aiosqlite:///./radegast.db` | Async SQLAlchemy database URL                                                                                                                      |
| `RADEGAST_CORS_ORIGINS`                  | N        | `http://localhost:5173,...`         | Comma-separated list of allowed CORS origins                                                                                                       |
| `RADEGAST_BASE_URL`                      | N        | `http://localhost:8000`             | Public base URL of the API server (used in emails and install scripts)                                                                             |
| `RADEGAST_UPLOAD_DIR`                    | N        | `uploads/packs`                     | Directory where uploaded configuration packs are stored                                                                                            |
| `RADEGAST_RELEASES_DIR`                  | N        | `agent/releases`                    | Directory containing Rustinel agent release binaries                                                                                               |
| `RADEGAST_SMTP_HOST`                     | N        | _(none)_                            | Outgoing SMTP mail server. If not set, emails are logged to stdout instead of being sent (useful for development).                                 |
| `RADEGAST_SMTP_PORT`                     | N        | `587`                               | Outgoing SMTP server port                                                                                                                          |
| `RADEGAST_SMTP_USER`                     | N        | _(empty)_                           | SMTP authentication username                                                                                                                       |
| `RADEGAST_SMTP_PASSWORD`                 | N        | _(empty)_                           | SMTP authentication password                                                                                                                       |
| `RADEGAST_SMTP_FROM`                     | N        | `noreply@radegast.local`            | Sender address for outgoing emails                                                                                                                 |
| `RADEGAST_SESSION_COOKIE_NAME`           | N        | `radegast_session`                  | Name of the session cookie                                                                                                                         |
| `RADEGAST_SESSION_MAX_AGE`               | N        | `604800`                            | Session lifetime in seconds (default: 7 days)                                                                                                      |
| `RADEGAST_WEB_UI_URL`                    | N        | _(empty)_                           | Optional URL of the web UI in case it is hosted elsewhere (used for WebAuthn origins and email links).                                             |
| `RADEGAST_TURNSTILE_SITE_KEY`            | N        | _(empty)_                           | Cloudflare Turnstile Site Key for optional registration-protection                                                                                 |
| `RADEGAST_TURNSTILE_SECRET_KEY`          | N        | _(empty)_                           | Cloudflare Turnstile Secret Key for verifying Turnstile responses                                                                                  |
| `RADEGAST_EMAIL_DEBOUNCE_SECONDS`        | N        | `180`                               | Email debounce limit in seconds before sending queued emails                                                                                       |
| `RADEGAST_EMAIL_BULK_INTERVALS`          | N        | `3,3,6,16,37,62,122,193`            | Comma-separated list of bulk debounce intervals in minutes                                                                                         |
| `RADEGAST_EMAIL_BULK_RESET_HOURS`        | N        | `24`                                | Time window in hours after which the email bulk sequence resets if no events occur                                                                 |
| `RADEGAST_ENABLE_EMAIL_WORKER`           | N        | `true`                              | Boolean flag to enable background email sending worker loop                                                                                        |
| `RADEGAST_WORKER_LOCK_PATH`              | N        | `/tmp/radegast-console.lock`        | Path to the shared file lock used by single-thread workers                                                                                         |
| `RADEGAST_MFA_REQUIRED_LEVEL_ADMIN`      | N        | `hardware_token`                    | Required MFA level for Admin accounts (none, otp, hardware_token)                                                                                  |
| `RADEGAST_MFA_REQUIRED_LEVEL_MAINTAINER` | N        | `none`                              | Required MFA level for Maintainer accounts (none, otp, hardware_token)                                                                             |
| `RADEGAST_MFA_REQUIRED_LEVEL_USER`       | N        | `none`                              | Required MFA level for User accounts (none, otp, hardware_token)                                                                                   |
| `RADEGAST_WEBAUTHN_RP_ID`                | N        | _(empty)_                           | Optional WebAuthn RP ID override. Set this to a shared parent domain (for example `radegast.app`) when API and Web UI run on different subdomains. |
| `RADEGAST_WEBAUTHN_ORIGINS`              | N        | _(empty)_                           | Optional comma-separated extra WebAuthn origins allowed during verification (for example `https://console.radegast.app`).                          |
| `RADEGAST_PACK_MAX_SIZE_MB`              | N        | _(none)_                            | General maximum pack zip size in MB. Applies to all roles unless a role-specific override is set. If unset, no size limit is enforced.             |
| `RADEGAST_PACK_MAX_SIZE_MB_USER`         | N        | _(none)_                            | Maximum pack zip size in MB for regular users. Falls back to `RADEGAST_PACK_MAX_SIZE_MB` if unset.                                                 |
| `RADEGAST_PACK_MAX_SIZE_MB_MAINTAINER`   | N        | _(none)_                            | Maximum pack zip size in MB for maintainer accounts. Falls back to `RADEGAST_PACK_MAX_SIZE_MB` if unset.                                           |
| `RADEGAST_PACK_MAX_SIZE_MB_ADMIN`        | N        | _(none)_                            | Maximum pack zip size in MB for admin accounts. Falls back to `RADEGAST_PACK_MAX_SIZE_MB` if unset.                                                |
