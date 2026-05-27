import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestRegistration:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "new@example.com"
        assert data["verified"] is False

    async def test_register_duplicate_email(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "Password123!"},
        )
        resp = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestVerification:
    async def test_verify_valid_token(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={"email": "verify@example.com", "password": "Password123!"},
        )
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": "verify@example.com"}, salt="email-verify")
        resp = await client.get(f"/auth/verify?token={token}")
        assert resp.status_code == 200

    async def test_verify_invalid_token(self, client: AsyncClient):
        resp = await client.get("/auth/verify?token=invalid-token")
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestLogin:
    async def test_login_success(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json=registered_user)
        assert resp.status_code == 200
        assert "radegast_session" in resp.cookies

    async def test_login_wrong_password(self, client: AsyncClient, registered_user):
        resp = await client.post(
            "/auth/login",
            json={"email": registered_user["email"], "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_login_unverified(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={"email": "unverified@example.com", "password": "Password123!"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "unverified@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestLogout:
    async def test_logout(self, auth_client: AsyncClient):
        resp = await auth_client.post("/auth/logout")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestMe:
    async def test_me_authenticated(self, auth_client: AsyncClient):
        resp = await auth_client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "test@example.com"

    async def test_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/auth/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestSessionInvalidation:
    async def test_session_invalid_after_password_change(self, client: AsyncClient, db_engine):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy import select
        from app.models.user import User
        from app.services.auth import create_signed_token, hash_password
        from datetime import datetime, timedelta

        email = "sesstest@example.com"
        password = "Password123!"

        await client.post("/auth/register", json={"email": email, "password": password})
        token = create_signed_token({"email": email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")
        resp = await client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200

        # Simulate password change (set password_change to future)
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one()
            user.password_change = datetime.utcnow() + timedelta(hours=1)
            await session.commit()

        # Session should now be invalid
        resp = await client.get("/auth/me")
        assert resp.status_code == 401
