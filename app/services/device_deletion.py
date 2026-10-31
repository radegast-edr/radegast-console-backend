import asyncio
import logging

from filelock import Timeout
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.database
from app.config import settings
from app.models.associations import device_group_devices
from app.models.device import Device
from app.models.exclusion import Exclusion
from app.models.log import Log, LogSeen
from app.utils import get_worker_lock

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


async def delete_device_logs_and_device(device_id: int, session: AsyncSession) -> None:
    """
    Delete logs for a deleted device in batches of BATCH_SIZE to avoid database locking/timeouts,
    clearing Exclusion.alert_id and LogSeen references first. Once all logs are gone, delete
    the device group associations and the device itself.
    """
    logger.info(f"Starting background cleanup for deleted device ID {device_id}...")

    # Batch delete logs
    while True:
        log_ids_res = await session.execute(select(Log.id).where(Log.device_id == device_id).limit(BATCH_SIZE))
        log_ids = log_ids_res.scalars().all()
        if not log_ids:
            break

        # Nullify alert_id on Exclusion
        await session.execute(update(Exclusion).where(Exclusion.alert_id.in_(log_ids)).values(alert_id=None))

        # Delete LogSeen entries
        await session.execute(delete(LogSeen).where(LogSeen.log_id.in_(log_ids)))

        # Delete Logs
        await session.execute(delete(Log).where(Log.id.in_(log_ids)))
        await session.commit()
        logger.debug(f"Deleted batch of {len(log_ids)} logs for device {device_id}")

    # Remove device group associations
    await session.execute(delete(device_group_devices).where(device_group_devices.c.device_id == device_id))

    # Delete device
    await session.execute(delete(Device).where(Device.id == device_id))
    await session.commit()
    logger.info(f"Successfully finished background cleanup for deleted device ID {device_id}")


async def process_deleted_devices(session: AsyncSession | None = None) -> None:
    """Find all devices marked with deleted_at and delete their logs and device records."""
    if session is not None:
        result = await session.execute(select(Device.id).where(Device.deleted_at.isnot(None)))
        deleted_device_ids = result.scalars().all()
        for dev_id in deleted_device_ids:
            try:
                await delete_device_logs_and_device(dev_id, session)
            except Exception as e:
                logger.error(f"Failed to clean up deleted device {dev_id}: {e}")
                await session.rollback()
    else:
        async with app.database.async_session() as s:
            result = await s.execute(select(Device.id).where(Device.deleted_at.isnot(None)))
            deleted_device_ids = result.scalars().all()
            for dev_id in deleted_device_ids:
                try:
                    await delete_device_logs_and_device(dev_id, s)
                except Exception as e:
                    logger.error(f"Failed to clean up deleted device {dev_id}: {e}")
                    await s.rollback()


async def process_deleted_devices_loop() -> None:
    """Background worker loop running every configured minutes to clean up deleted devices."""
    lock = get_worker_lock()
    while True:
        try:
            async with lock:
                await process_deleted_devices()
        except Timeout:
            pass
        except Exception as e:
            logger.error(f"[DEVICE DELETION WORKER ERROR] {e}")
        await asyncio.sleep(max(1, settings.device_deletion_interval_minutes) * 60)
