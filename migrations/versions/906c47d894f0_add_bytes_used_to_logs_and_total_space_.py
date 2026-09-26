"""add_bytes_used_to_logs_and_total_space_used_to_devices

Revision ID: 906c47d894f0
Revises: 1140a2fe5b93
Create Date: 2026-09-26 15:13:14.930941

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '906c47d894f0'
down_revision: Union[str, Sequence[str], None] = '1140a2fe5b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    needs_backfill = False

    if "logs" in tables:
        columns = [c["name"] for c in inspector.get_columns("logs")]
        if "bytes_used" not in columns:
            with op.batch_alter_table("logs", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "bytes_used",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    )
                )
            needs_backfill = True

    if "devices" in tables:
        columns = [c["name"] for c in inspector.get_columns("devices")]
        if "total_space_used" not in columns:
            with op.batch_alter_table("devices", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "total_space_used",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    )
                )
            needs_backfill = True

    if "logs" in tables and "devices" in tables:
        # If columns already existed (e.g. from an earlier interrupted run),
        # check if there are any unmigrated logs with bytes_used = 0 or NULL
        if not needs_backfill:
            has_unmigrated = conn.execute(
                sa.text("SELECT 1 FROM logs WHERE bytes_used = 0 OR bytes_used IS NULL LIMIT 1")
            ).first()
            if has_unmigrated:
                needs_backfill = True

        if needs_backfill:
            # Reset total_space_used to 0 before batched recalculation to ensure clean state
            conn.execute(sa.text("UPDATE devices SET total_space_used = 0"))
            if hasattr(conn, "commit"):
                conn.commit()

            BATCH_SIZE = 1000
            last_id = 0
            while True:
                rows = conn.execute(
                    sa.text(
                        "SELECT id, device_id, COALESCE(LENGTH(content), 0) "
                        "FROM logs WHERE id > :last_id ORDER BY id ASC LIMIT :batch_size"
                    ),
                    {"last_id": last_id, "batch_size": BATCH_SIZE},
                ).fetchall()
                if not rows:
                    break

                min_id = rows[0][0]
                max_id = rows[-1][0]

                # Update bytes_used for all logs in this batch
                conn.execute(
                    sa.text(
                        "UPDATE logs SET bytes_used = COALESCE(LENGTH(content), 0) "
                        "WHERE id >= :min_id AND id <= :max_id"
                    ),
                    {"min_id": min_id, "max_id": max_id},
                )

                # Aggregate space used per device for this batch
                device_deltas: dict[int, int] = {}
                for _, dev_id, log_len in rows:
                    if dev_id is not None:
                        device_deltas[dev_id] = device_deltas.get(dev_id, 0) + (log_len or 0)

                # Add space to device totals
                if device_deltas:
                    conn.execute(
                        sa.text(
                            "UPDATE devices SET total_space_used = COALESCE(total_space_used, 0) + :delta "
                            "WHERE id = :device_id"
                        ),
                        [{"delta": delta, "device_id": dev_id} for dev_id, delta in device_deltas.items()],
                    )

                if hasattr(conn, "commit"):
                    conn.commit()

                last_id = max_id


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "devices" in tables:
        columns = [c["name"] for c in inspector.get_columns("devices")]
        if "total_space_used" in columns:
            with op.batch_alter_table("devices", schema=None) as batch_op:
                batch_op.drop_column("total_space_used")

    if "logs" in tables:
        columns = [c["name"] for c in inspector.get_columns("logs")]
        if "bytes_used" in columns:
            with op.batch_alter_table("logs", schema=None) as batch_op:
                batch_op.drop_column("bytes_used")
