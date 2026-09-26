import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.database import get_db
from app.main import app


@pytest.mark.asyncio
class TestHealth:
    async def test_health_check(self, client: AsyncClient):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "database": "ok"}

    async def test_health_check_database_failure(self, client: AsyncClient):
        mock_session = AsyncMock()
        mock_session.execute.side_effect = ConnectionRefusedError("Database connection refused")

        async def failing_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = failing_get_db
        try:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 503
            data = resp.json()
            assert data == {"status": "error", "database": "unavailable"}
            assert data["status"] != "ok"
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_health_check_database_timeout(self, client: AsyncClient):
        mock_session = AsyncMock()

        async def slow_execute(*_args, **_kwargs):
            await asyncio.sleep(10)

        mock_session.execute.side_effect = slow_execute

        async def slow_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = slow_get_db
        try:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 503
            data = resp.json()
            assert data == {"status": "error", "database": "unavailable"}
            assert data["status"] != "ok"
        finally:
            app.dependency_overrides.pop(get_db, None)
