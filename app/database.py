from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    from app.models import (  # noqa: F401
        Device,
        DeviceGroup,
        KeyTransfer,
        Log,
        Pack,
        PackEnabled,
        PackVersion,
        PublicKey,
        Team,
        User,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Migrate existing databases: add notification preference columns if missing
    _notification_cols = [
        ("notify_login", "BOOLEAN NOT NULL DEFAULT 1"),
        ("notify_new_keys", "BOOLEAN NOT NULL DEFAULT 1"),
        ("notify_recovery_used", "BOOLEAN NOT NULL DEFAULT 1"),
        ("notify_keys_transferred", "BOOLEAN NOT NULL DEFAULT 1"),
        ("notify_device_log", "BOOLEAN NOT NULL DEFAULT 0"),
    ]
    async with engine.begin() as conn:
        for col_name, col_def in _notification_cols:
            try:
                await conn.execute(
                    text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                )
            except Exception:
                pass  # column already exists

    _public_key_cols = [
        ("name", "TEXT"),
        ("last_used_at", "DATETIME"),
    ]
    async with engine.begin() as conn:
        for col_name, col_def in _public_key_cols:
            try:
                await conn.execute(
                    text(f"ALTER TABLE public_keys ADD COLUMN {col_name} {col_def}")
                )
            except Exception:
                pass  # column already exists
