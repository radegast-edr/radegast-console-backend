from httpx import AsyncClient
import pytest


@pytest.mark.asyncio
class TestHealth:
    async def test_health_check(self, client: AsyncClient):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
