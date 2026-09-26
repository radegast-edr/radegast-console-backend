#!/usr/bin/env python3
"""Migrate data from a local SQLite database to an empty MySQL database.

Usage:
    uv run python migrate-to-mysql.py --mysql-url "mysql+aiomysql://user:pass@host/db?ssl_ca=cert.pem"
    uv run python migrate-to-mysql.py --sqlite-path radegast.db --mysql-url "..."

Or set RADEGAST_DATABASE_URL as an environment variable:
    export RADEGAST_DATABASE_URL="mysql+aiomysql://..."
    uv run python migrate-to-mysql.py
"""

import argparse
import os
import re
import sqlite3
import ssl
import subprocess
import sys
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pymysql

# Tables to skip — internal SQLite artifacts or Alembic-managed
SKIP_TABLES = {"sqlite_sequence", "_alembic_tmp_logs", "alembic_version", "yubikeys"}

# Topological insertion order respecting foreign key dependencies.
# Because we disable FK checks, this is mostly for readability / debugging.
TABLE_ORDER = [
    "users",
    "teams",
    "device_groups",
    "devices",
    "packs",
    "pack_versions",
    "pack_version_rules",
    "exclusions",
    "logs",
    "logs_seen",
    "team_users",
    "team_device_groups",
    "team_invitations",
    "device_group_devices",
    "pack_enabled",
    "pack_teams",
    "public_keys",
    "key_transfers",
    "api_keys",
    "hardware_tokens",
    "email_bulk_states",
    "queued_emails",
    "prevention_allowlists",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate data from SQLite to MySQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sqlite-path",
        default="radegast.db",
        help="Path to the source SQLite database (default: radegast.db)",
    )
    parser.add_argument(
        "--mysql-url",
        default=os.environ.get("RADEGAST_DATABASE_URL"),
        help=("MySQL connection URL in SQLAlchemy format. Falls back to RADEGAST_DATABASE_URL env var."),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate existing MySQL tables before inserting (for re-runs).",
    )
    parser.add_argument(
        "--skip-alembic",
        action="store_true",
        help="Skip running Alembic migrations (assumes schema already exists).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of rows per INSERT transaction batch (default: 50).",
    )
    return parser.parse_args()


def parse_mysql_url(url: str) -> tuple[dict, ssl.SSLContext | None]:
    """Parse a SQLAlchemy-style MySQL URL into pymysql connection kwargs and SSL context.

    Handles the ``mysql+aiomysql://`` or ``mysql+pymysql://`` prefix and extracts
    SSL query parameters into a proper ``ssl.SSLContext``.
    """
    # Strip the SQLAlchemy dialect prefix to get a parseable URL
    clean_url = re.sub(r"^mysql\+\w+://", "mysql://", url)

    parsed = urlparse(clean_url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    # Flatten single-value lists
    query_flat = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}

    # Extract SSL parameters
    ssl_ca = query_flat.pop("ssl_ca", None)
    ssl_cert = query_flat.pop("ssl_cert", None)
    ssl_key = query_flat.pop("ssl_key", None)
    ssl_verify_cert = query_flat.pop("ssl_verify_cert", None)
    ssl_verify_identity = query_flat.pop("ssl_verify_identity", None)
    query_flat.pop("ssl_mode", None)
    query_flat.pop("ssl", None)

    ssl_ctx = None
    if ssl_ca or ssl_cert or ssl_verify_cert:
        ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

        if ssl_ca:
            ssl_ctx.load_verify_locations(cafile=ssl_ca)
        if ssl_cert:
            ssl_ctx.load_cert_chain(certfile=ssl_cert, keyfile=ssl_key)

        # Identity verification will fail when connecting by IP address
        # if the server cert CN doesn't match the IP.
        is_verify_identity_true = isinstance(ssl_verify_identity, str) and ssl_verify_identity.lower() in (
            "true",
            "1",
            "yes",
        )
        if is_verify_identity_true:
            print("WARNING: ssl_verify_identity=true is set but will likely fail when connecting by IP address. Overriding to false.")
        ssl_ctx.check_hostname = False

        is_verify_cert_false = isinstance(ssl_verify_cert, str) and ssl_verify_cert.lower() in (
            "false",
            "0",
            "no",
        )
        if is_verify_cert_false:
            ssl_ctx.verify_mode = ssl.CERT_NONE

    connect_kwargs = {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
        "charset": "utf8mb4",
    }

    return connect_kwargs, ssl_ctx


def build_alembic_mysql_url(original_url: str) -> str:
    """Build a clean MySQL URL for Alembic (strip SSL params that aiomysql can't handle).

    The SSL context is handled by ``prepare_database_url_and_connect_args`` in
    ``app/database.py`` which Alembic's ``env.py`` calls automatically.
    """
    return original_url


def run_alembic_migrations(mysql_url: str) -> None:
    """Run ``alembic upgrade head`` against the MySQL database."""
    print("\n--- Running Alembic migrations on MySQL ---")
    env = os.environ.copy()
    env["RADEGAST_DATABASE_URL"] = mysql_url
    env["RADEGAST_ENVIRONMENT"] = "dev"  # suppress secret key warning

    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        # Alembic logs to stderr
        for line in result.stderr.splitlines():
            print(f"  {line}")

    if result.returncode != 0:
        print(f"ERROR: Alembic migration failed with exit code {result.returncode}")
        sys.exit(1)

    print("Alembic migrations completed successfully.\n")


def get_sqlite_tables(sqlite_conn: sqlite3.Connection) -> list[str]:
    """Get all application table names from SQLite, excluding internal ones."""
    cursor = sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    all_tables = [row[0] for row in cursor.fetchall()]
    return [t for t in all_tables if t not in SKIP_TABLES]


def get_column_names(sqlite_conn: sqlite3.Connection, table: str) -> list[str]:
    """Get column names for a table from SQLite."""
    cursor = sqlite_conn.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cursor.fetchall()]


def get_datetime_columns(sqlite_conn: sqlite3.Connection, table: str) -> set[int]:
    """Get column indices that have DATETIME type in SQLite."""
    cursor = sqlite_conn.execute(f'PRAGMA table_info("{table}")')
    indices = set()
    for row in cursor.fetchall():
        col_index = row[0]
        col_type = row[2].upper()
        if "DATE" in col_type or "TIME" in col_type:
            indices.add(col_index)
    return indices


def fix_datetime(value: str | None) -> datetime | None:
    """Convert SQLite datetime string to a Python datetime object.

    SQLite stores datetimes as strings in various formats:
    - '2026-05-29 14:31:41.730256Z'  (with Z suffix)
    - '2026-06-09 08:21:59.000000'   (without Z)
    - '2026-05-29 14:31:41'          (no microseconds)
    """
    if value is None:
        return None

    # Strip trailing Z (UTC indicator — MySQL DATETIME doesn't accept it)
    value = value.rstrip("Z")

    # Try parsing with microseconds first, then without
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Return as-is if no format matches (shouldn't happen)
    print(f"  WARNING: Could not parse datetime value: {value!r}")
    return value


def transform_row(row: tuple, datetime_col_indices: set[int]) -> tuple:
    """Transform a SQLite row for MySQL insertion."""
    if not datetime_col_indices:
        return row

    row_list = list(row)
    for idx in datetime_col_indices:
        if idx < len(row_list) and isinstance(row_list[idx], str):
            row_list[idx] = fix_datetime(row_list[idx])
    return tuple(row_list)


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    mysql_conn: pymysql.Connection,
    table: str,
    force: bool,
    batch_size: int = 50,
) -> int:
    """Migrate a single table from SQLite to MySQL. Returns the number of rows migrated."""
    columns = get_column_names(sqlite_conn, table)
    datetime_indices = get_datetime_columns(sqlite_conn, table)

    if not columns:
        print(f"  {table}: no columns found, skipping")
        return 0

    # Check if MySQL table exists and has data
    with mysql_conn.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")  # noqa: S608
        existing_count = cursor.fetchone()[0]

    if existing_count > 0:
        if force:
            print(f"  {table}: truncating {existing_count} existing rows (--force)")
            try:
                with mysql_conn.cursor() as cursor:
                    cursor.execute(f"TRUNCATE TABLE `{table}`")
            except pymysql.err.OperationalError:
                with mysql_conn.cursor() as cursor:
                    cursor.execute(f"DELETE FROM `{table}`")  # noqa: S608
            mysql_conn.commit()
        else:
            print(f"  {table}: SKIPPED — already has {existing_count} rows (use --force to overwrite)")
            return 0

    # Read all rows from SQLite
    sqlite_cursor = sqlite_conn.execute(f'SELECT * FROM "{table}"')  # noqa: S608
    rows = sqlite_cursor.fetchall()

    if not rows:
        print(f"  {table}: 0 rows (empty)")
        return 0

    # Transform rows (fix datetimes)
    transformed = [transform_row(row, datetime_indices) for row in rows]

    # Build INSERT statement
    col_list = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})"  # noqa: S608

    # Batch insert in chunks to avoid exceeding max_binlog_cache_size
    for i in range(0, len(transformed), batch_size):
        chunk = transformed[i : i + batch_size]
        with mysql_conn.cursor() as cursor:
            cursor.executemany(insert_sql, chunk)
        mysql_conn.commit()

    print(f"  {table}: {len(transformed)} rows migrated")
    return len(transformed)


def verify_counts(
    sqlite_conn: sqlite3.Connection,
    mysql_conn: pymysql.Connection,
    tables: list[str],
) -> bool:
    """Verify that row counts match between SQLite and MySQL."""
    print("\n--- Verification ---")
    all_ok = True
    header = f"{'Table':<30} {'SQLite':>8} {'MySQL':>8} {'Status':>8}"
    print(header)
    print("-" * len(header))

    for table in tables:
        sqlite_cursor = sqlite_conn.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608
        sqlite_count = sqlite_cursor.fetchone()[0]

        try:
            with mysql_conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")  # noqa: S608
                mysql_count = cursor.fetchone()[0]
        except pymysql.err.ProgrammingError:
            mysql_count = "N/A"

        if mysql_count == sqlite_count:
            status = "OK"
        else:
            status = "MISMATCH"
            all_ok = False

        print(f"  {table:<28} {sqlite_count:>8} {mysql_count!s:>8} {status:>8}")

    return all_ok


def main() -> None:
    args = parse_args()

    if not args.mysql_url:
        print("ERROR: --mysql-url is required (or set RADEGAST_DATABASE_URL env var)")
        sys.exit(1)

    if not os.path.exists(args.sqlite_path):
        print(f"ERROR: SQLite database not found: {args.sqlite_path}")
        sys.exit(1)

    print(f"Source:  {args.sqlite_path}")
    print(f"Target:  {args.mysql_url.split('@')[1] if '@' in args.mysql_url else args.mysql_url}")

    # Step 1: Run Alembic migrations to create schema
    if not args.skip_alembic:
        run_alembic_migrations(args.mysql_url)

    # Step 2: Connect to SQLite
    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_conn.execute("PRAGMA foreign_keys = OFF")

    # Step 3: Connect to MySQL
    connect_kwargs, ssl_ctx = parse_mysql_url(args.mysql_url)
    if ssl_ctx:
        connect_kwargs["ssl"] = ssl_ctx

    try:
        mysql_conn = pymysql.connect(**connect_kwargs)
    except pymysql.err.OperationalError as e:
        print(f"ERROR: Could not connect to MySQL: {e}")
        sys.exit(1)

    print("Connected to MySQL successfully.\n")

    # Step 4: Determine table order
    sqlite_tables = set(get_sqlite_tables(sqlite_conn))

    # Use the predefined order, then add any tables we missed
    ordered_tables = [t for t in TABLE_ORDER if t in sqlite_tables]
    remaining = sorted(sqlite_tables - set(ordered_tables))
    ordered_tables.extend(remaining)

    print(f"Tables to migrate: {len(ordered_tables)}")

    # Step 5: Disable FK checks and migrate
    print("\n--- Migrating data ---")
    with mysql_conn.cursor() as cursor:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")

    total_rows = 0
    errors = []
    for table in ordered_tables:
        try:
            count = migrate_table(sqlite_conn, mysql_conn, table, args.force, args.batch_size)
            total_rows += count
        except Exception as e:
            print(f"  {table}: ERROR — {e}")
            errors.append((table, str(e)))
            mysql_conn.rollback()

    # Step 6: Re-enable FK checks
    with mysql_conn.cursor() as cursor:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    # Step 7: Verify
    all_ok = verify_counts(sqlite_conn, mysql_conn, ordered_tables)

    # Summary
    print("\n--- Summary ---")
    print(f"Total rows migrated: {total_rows}")
    if errors:
        print(f"Errors: {len(errors)}")
        for table, err in errors:
            print(f"  {table}: {err}")
    if all_ok:
        print("Verification: ALL OK")
    else:
        print("Verification: MISMATCHES FOUND (see above)")

    sqlite_conn.close()
    mysql_conn.close()


if __name__ == "__main__":
    main()
