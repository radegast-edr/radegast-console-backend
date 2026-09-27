import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings


@pytest.fixture
def clean_env():
    """Fixture to backup and restore the database URL environment variable and settings singleton."""
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


def _get_sqlite_version(db_file: Path) -> str | None:
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        rows = cursor.execute("SELECT version_num FROM alembic_version").fetchall()
        return rows[0][0] if rows else None


def test_sqlite_migrations(clean_env):
    """Test upgrade and downgrade path for SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_migration.db"
        db_url = f"sqlite+aiosqlite:///{db_file}"

        os.environ["RADEGAST_DATABASE_URL"] = db_url
        settings.database_url = db_url

        # Load Alembic configuration
        config = Config("alembic.ini")
        script = ScriptDirectory.from_config(config)
        head_rev = script.get_current_head()

        # Upgrade to head
        command.upgrade(config, "head")
        assert _get_sqlite_version(db_file) == head_rev

        # Downgrade to base
        command.downgrade(config, "base")
        assert _get_sqlite_version(db_file) is None

        # Upgrade to head again
        command.upgrade(config, "head")
        assert _get_sqlite_version(db_file) == head_rev

        # Upgrade to head once more should be an idempotent no-op
        command.upgrade(config, "head")
        assert _get_sqlite_version(db_file) == head_rev


def test_sqlite_migration_with_preexisting_data(clean_env):
    """Test upgrade with pre-existing data (including empty logs) and verify version stamping."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_migration_data.db"
        db_url = f"sqlite+aiosqlite:///{db_file}"

        os.environ["RADEGAST_DATABASE_URL"] = db_url
        settings.database_url = db_url

        config = Config("alembic.ini")
        script = ScriptDirectory.from_config(config)
        head_rev = script.get_current_head()

        # Upgrade up to the revision before 906c47d894f0
        command.upgrade(config, "1140a2fe5b93")
        assert _get_sqlite_version(db_file) == "1140a2fe5b93"

        # Seed pre-existing device and log data (including an empty-content log)
        with sqlite3.connect(db_file) as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO devices (name, token, signature_public_key) VALUES ('dev1', 'tok1', 'key1')")
            d1 = cur.lastrowid
            cur.execute("INSERT INTO devices (name, token, signature_public_key) VALUES ('dev2', 'tok2', 'key2')")
            d2 = cur.lastrowid
            # Empty log: length 0
            cur.execute("INSERT INTO logs (device_id, content, time) VALUES (?, '', '2026-01-01')", (d1,))
            # Non-empty log: length 10
            cur.execute("INSERT INTO logs (device_id, content, time) VALUES (?, '0123456789', '2026-01-01')", (d2,))
            conn.commit()

        # Upgrade to head (runs 906c47d894f0 with backfill)
        command.upgrade(config, "head")
        assert _get_sqlite_version(db_file) == head_rev

        # Verify backfilled values
        with sqlite3.connect(db_file) as conn:
            cur = conn.cursor()
            dev_totals = dict(cur.execute("SELECT id, total_space_used FROM devices").fetchall())
            assert dev_totals[d1] == 0
            assert dev_totals[d2] == 10

            log_bytes = cur.execute("SELECT id, bytes_used FROM logs ORDER BY id").fetchall()
            assert log_bytes[0][1] == 0
            assert log_bytes[1][1] == 10

        # Repeated upgrade must remain at head and be an idempotent no-op
        command.upgrade(config, "head")
        assert _get_sqlite_version(db_file) == head_rev


def test_mysql_migrations(clean_env):
    """Test upgrade and downgrade path for MySQL if connection URL is provided."""
    mysql_url = os.environ.get("RADEGAST_TEST_MYSQL_URL")
    if not mysql_url:
        pytest.skip("RADEGAST_TEST_MYSQL_URL is not set. Skipping MySQL migrations test.")

    os.environ["RADEGAST_DATABASE_URL"] = mysql_url
    settings.database_url = mysql_url

    config = Config("alembic.ini")

    # Upgrade to head
    command.upgrade(config, "head")

    # Downgrade to base
    command.downgrade(config, "base")

    # Upgrade to head again
    command.upgrade(config, "head")
