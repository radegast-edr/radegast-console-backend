"""add_device_healthy

Revision ID: b4c5d6e7f8a9
Revises: f8eccd8a7d8c
Create Date: 2026-08-16 15:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'f8eccd8a7d8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "devices" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("devices")]
        if "healthy" not in columns:
            with op.batch_alter_table("devices", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "healthy",
                        sa.Boolean(),
                        nullable=True,
                    )
                )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "devices" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("devices")]
        if "healthy" in columns:
            with op.batch_alter_table("devices", schema=None) as batch_op:
                batch_op.drop_column("healthy")
