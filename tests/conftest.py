import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

@pytest.fixture(scope="session", autouse=True)
def setup_test_uploads():
    import tempfile
    import shutil
    from pathlib import Path
    from app.config import settings

    old_upload_dir = settings.upload_dir
    test_dir = tempfile.mkdtemp()
    settings.upload_dir = test_dir
    Path(test_dir).mkdir(parents=True, exist_ok=True)
    yield
    settings.upload_dir = old_upload_dir
    shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def setup_test_releases():
    """Create a temporary releases directory with a stub zip so the agent
    download endpoint returns 200 without requiring real release binaries."""
    import io
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path
    from app.config import settings
    import app.routers.install as install_router

    old_releases_dir = settings.releases_dir
    old_releases_path = install_router.RELEASES_DIR

    test_dir = Path(tempfile.mkdtemp())
    settings.releases_dir = str(test_dir)
    install_router.RELEASES_DIR = test_dir

    # Create a minimal zip at the expected path: <version>/<os>/<arch>/rustinel.zip
    stub_version = "0.0.1"
    stub_zip_path = test_dir / stub_version / "linux" / "amd64" / "rustinel.zip"
    stub_zip_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rustinel", b"stub")
    stub_zip_path.write_bytes(buf.getvalue())

    yield

    settings.releases_dir = old_releases_dir
    install_router.RELEASES_DIR = old_releases_path
    shutil.rmtree(test_dir, ignore_errors=True)


from app.config import settings
settings.enable_email_worker = False

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    import app.database
    original_session = app.database.async_session
    app.database.async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine

    app.database.async_session = original_session

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

    from app.dependencies import rate_limit_login, rate_limit_mfa, rate_limit_mfa_otp

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[rate_limit_login] = lambda: None
    app.dependency_overrides[rate_limit_mfa] = lambda: None
    app.dependency_overrides[rate_limit_mfa_otp] = lambda: None

    class TestAPIPrefixMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "")
                if not (
                    path.startswith("/api/v1")
                    or path in ("/favicon.ico", "/.well-known/security.txt")
                    or path.startswith("/ui")
                ):
                    scope["path"] = f"/api/v1{path}"
                    raw_path = scope.get("raw_path", b"")
                    if raw_path:
                        scope["raw_path"] = b"/api/v1" + raw_path
            await self.app(scope, receive, send)

    transport = ASGITransport(app=TestAPIPrefixMiddleware(app))
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
