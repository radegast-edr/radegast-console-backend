"""add_deleted_at_to_devices

Revision ID: b5b028633eda
Revises: b94f03618a0d
Create Date: 2026-10-31 18:39:26.802960

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b5b028633eda"
down_revision: Union[str, Sequence[str], None] = "b94f03618a0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "devices" in tables:
        columns = [c["name"] for c in inspector.get_columns("devices")]
        indexes = [idx["name"] for idx in inspector.get_indexes("devices")]
        with op.batch_alter_table("devices", schema=None) as batch_op:
            if "deleted_at" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "deleted_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )
            if "ix_devices_deleted_at" not in indexes:
                batch_op.create_index(
                    batch_op.f("ix_devices_deleted_at"),
                    ["deleted_at"],
                    unique=False,
                )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "devices" in tables:
        columns = [c["name"] for c in inspector.get_columns("devices")]
        indexes = [idx["name"] for idx in inspector.get_indexes("devices")]
        with op.batch_alter_table("devices", schema=None) as batch_op:
            if "ix_devices_deleted_at" in indexes:
                batch_op.drop_index(batch_op.f("ix_devices_deleted_at"))
            if "deleted_at" in columns:
                batch_op.drop_column("deleted_at")
