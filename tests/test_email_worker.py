import asyncio

import pytest

import app.services.email as email_service
from app.config import Settings, settings


def test_worker_lock_path_default_and_env_override(monkeypatch):
    default_settings = Settings()
    assert default_settings.worker_lock_path == "/tmp/radegast-console.lock"

    monkeypatch.setenv("RADEGAST_WORKER_LOCK_PATH", "/tmp/custom-radegast.lock")
    overridden_settings = Settings()
    assert overridden_settings.worker_lock_path == "/tmp/custom-radegast.lock"


@pytest.mark.asyncio
async def test_process_email_queue_loop_respects_shared_lock(monkeypatch, tmp_path):
    lock_path = tmp_path / "radegast-test.lock"
    monkeypatch.setattr(settings, "worker_lock_path", str(lock_path))

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    continue_first = asyncio.Event()

    call_count = 0

    async def fake_process_email_queue():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await continue_first.wait()
        else:
            second_started.set()
            return

    monkeypatch.setattr(email_service, "process_email_queue", fake_process_email_queue)
    monkeypatch.setattr(email_service.random, "randint", lambda a, b: 0)

    task1 = asyncio.create_task(email_service.process_email_queue_loop())
    await asyncio.wait_for(first_started.wait(), timeout=2.0)

    task2 = asyncio.create_task(email_service.process_email_queue_loop())
    await asyncio.sleep(0.1)
    assert call_count == 1
    assert not second_started.is_set()

    task1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task1

    await asyncio.wait_for(second_started.wait(), timeout=2.0)

    task2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task2
