import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient):
    """Register and verify a user, return login credentials."""
    email = "test@example.com"
    password = "TestPass123!"

    await client.post("/auth/register", json={"email": email, "password": password})

    # Manually verify via token
    from app.services.auth import create_signed_token
    token = create_signed_token({"email": email}, salt="email-verify")
    await client.get(f"/auth/verify?token={token}")

    return {"email": email, "password": password}


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient, registered_user):
    """Client with authenticated user session."""
    resp = await client.post("/auth/login", json=registered_user)
    assert resp.status_code == 200
    # Cookies are stored on the client
    return client


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, db_engine):
    """Client with authenticated admin user."""
    from sqlalchemy import select
    from app.models.user import User, UserRole
    from app.services.auth import hash_password, create_signed_token
    from datetime import datetime

    email = "admin@example.com"
    password = "AdminPass123!"

    # Register
    await client.post("/auth/register", json={"email": email, "password": password})

    # Verify
    token = create_signed_token({"email": email}, salt="email-verify")
    await client.get(f"/auth/verify?token={token}")

    # Promote to admin
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = UserRole.admin
        await session.commit()

    # Login
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200

    return client


@pytest_asyncio.fixture
async def maintainer_client(client: AsyncClient, db_engine):
    """Client with authenticated maintainer user."""
    from sqlalchemy import select
    from app.models.user import User, UserRole
    from app.services.auth import create_signed_token

    email = "maintainer@example.com"
    password = "MaintainerPass123!"

    await client.post("/auth/register", json={"email": email, "password": password})

    token = create_signed_token({"email": email}, salt="email-verify")
    await client.get(f"/auth/verify?token={token}")

    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = UserRole.maintainer
        await session.commit()

    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200

    return client
