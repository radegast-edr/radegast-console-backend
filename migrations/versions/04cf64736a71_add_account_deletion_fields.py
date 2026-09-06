"""add_account_deletion_fields

Revision ID: 04cf64736a71
Revises: b4c5d6e7f8a9
Create Date: 2026-09-05 12:19:50.169163

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04cf64736a71'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "users" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("users")]
        with op.batch_alter_table("users", schema=None) as batch_op:
            if "deletion_requested_at" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "deletion_requested_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )
            if "deletion_scheduled_at" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "deletion_scheduled_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "users" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("users")]
        with op.batch_alter_table("users", schema=None) as batch_op:
            if "deletion_scheduled_at" in columns:
                batch_op.drop_column("deletion_scheduled_at")
            if "deletion_requested_at" in columns:
                batch_op.drop_column("deletion_requested_at")
