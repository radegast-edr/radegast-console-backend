from datetime import UTC, datetime
from pathlib import Path

from filelock import AsyncFileLock
from starlette.requests import Request

from app.config import settings


def get_client_ip(request: Request) -> str:
    """Return the real client IP, checking CF-Connecting-IP, X-Real-IP, and X-Forwarded-For first."""
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_worker_lock() -> AsyncFileLock:
    lock_path = Path(settings.worker_lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncFileLock(lock_path, timeout=0)
