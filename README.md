# Radegast EDR - Backend

Radegast EDR Backend is the management and orchestration server for the Radegast Endpoint Detection and Response (EDR) platform. Built with FastAPI and SQLAlchemy, it handles device authorization, user configuration packs, age-encrypted log storage, alert status tracking, and key/session management.

## Features

1. **Device Management**: Creating and enrolling EDR agent devices, assigning them to Groups, and generating secure authorization tokens.
2. **Configuration Packs**: Storing and distributing YAML/binary endpoint detection policies and versions.
3. **Encrypted Log Storage**: Accepting EDR agent telemetry/logs encrypted using `age`, verifying signatures, and managing user "seen" states for logged alerts.
4. **Team Collaboration**: Creating teams, managing device group permissions, and sending email notifications on critical events.

---

## Getting Started

### Prerequisites

Ensure you have Python 3.11+ and `uv` (recommended) or standard `pip` installed.

### Installation

1. Install project dependencies:
   ```bash
   uv sync
   # Or using standard pip with a virtual environment:
   # python -m venv .venv && source .venv/bin/activate && pip install .
   ```

2. Development tools installation:
   ```bash
   uv sync --only-group dev
   # Or:
   # pip install .[dev]
   ```

### Running the Backend

Start the development server with:
```bash
uvicorn app.main:app --reload --port 8000
```
By default, the server runs on [http://localhost:8000](http://localhost:8000). You can access the auto-generated Swagger documentation at [http://localhost:8000/docs](http://localhost:8000/docs).

### Configuration

You can configure the application using environment variables prefixed with `RADEGAST_`. Standard settings (defined in [config.py](file:///home/adam/app/app/config.py)) include:

| Environment Variable | Default Value | Description |
|---|---|---|
| `RADEGAST_DATABASE_URL` | `sqlite+aiosqlite:///./radegast_pack.db` | Async SQLAlchemy database URL |
| `RADEGAST_SECRET_KEY` | `change-me-in-production` | Secret key used for session signing |
| `RADEGAST_CORS_ORIGINS` | `http://localhost:5173` | Allowed CORS frontend origins |
| `RADEGAST_SMTP_HOST` | `localhost` | Outgoing SMTP mail server |
| `RADEGAST_SMTP_PORT` | `587` | Outgoing SMTP server port |
| `RADEGAST_BASE_URL` | `http://localhost:8000` | Base URL of the API server |

### Running Tests

Execute the complete asynchronous test suite using:
```bash
uv run pytest
# Or:
# pytest
```
