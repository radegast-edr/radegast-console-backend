import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def clean_env():
    """Fixture to backup and restore the database URL environment variable and settings singleton."""
    from app.config import settings
    orig_url = os.environ.get("RADEGAST_DATABASE_URL")
    orig_settings_url = settings.database_url
    orig_secret = os.environ.get("RADEGAST_SECRET_KEY")
    if not orig_secret:
        os.environ["RADEGAST_SECRET_KEY"] = "test-secret-key-for-migrations"

    yield

    settings.database_url = orig_settings_url
    if orig_url is not None:
        os.environ["RADEGAST_DATABASE_URL"] = orig_url
    elif "RADEGAST_DATABASE_URL" in os.environ:
        del os.environ["RADEGAST_DATABASE_URL"]

    if not orig_secret and "RADEGAST_SECRET_KEY" in os.environ:
        del os.environ["RADEGAST_SECRET_KEY"]


def test_sqlite_migrations(clean_env):
    """Test upgrade and downgrade path for SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_migration.db"
        db_url = f"sqlite+aiosqlite:///{db_file}"

        os.environ["RADEGAST_DATABASE_URL"] = db_url
        from app.config import settings
        settings.database_url = db_url

        # Load Alembic configuration
        config = Config("alembic.ini")

        # Upgrade to head
        command.upgrade(config, "head")

        # Downgrade to base
        command.downgrade(config, "base")

        # Upgrade to head again
        command.upgrade(config, "head")


def test_mysql_migrations(clean_env):
    """Test upgrade and downgrade path for MySQL if connection URL is provided."""
    mysql_url = os.environ.get("RADEGAST_TEST_MYSQL_URL")
    if not mysql_url:
        pytest.skip("RADEGAST_TEST_MYSQL_URL is not set. Skipping MySQL migrations test.")

    os.environ["RADEGAST_DATABASE_URL"] = mysql_url
    from app.config import settings
    settings.database_url = mysql_url

    config = Config("alembic.ini")

    # Upgrade to head
    command.upgrade(config, "head")

    # Downgrade to base
    command.downgrade(config, "base")

    # Upgrade to head again
    command.upgrade(config, "head")
