from datetime import datetime, timezone
from filelock import AsyncFileLock
from pathlib import Path

from app.config import settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_worker_lock() -> AsyncFileLock:
    lock_path = Path(settings.worker_lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncFileLock(lock_path, timeout=0)
