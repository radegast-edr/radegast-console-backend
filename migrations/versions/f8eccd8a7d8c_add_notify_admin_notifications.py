"""add_notify_admin_notifications

Revision ID: f8eccd8a7d8c
Revises: 3ff19315747c
Create Date: 2026-08-16 10:06:05.686663

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8eccd8a7d8c'
down_revision: Union[str, Sequence[str], None] = '3ff19315747c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "users" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "notify_admin_notifications" not in columns:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "notify_admin_notifications",
                        sa.Boolean(),
                        server_default="1",
                        nullable=False,
                    )
                )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "users" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "notify_admin_notifications" in columns:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.drop_column("notify_admin_notifications")
