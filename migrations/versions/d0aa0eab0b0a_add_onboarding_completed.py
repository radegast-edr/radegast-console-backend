"""add_onboarding_completed

Revision ID: d0aa0eab0b0a
Revises: 13d50945cf19
Create Date: 2026-07-03 20:11:35.672918

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0aa0eab0b0a'
down_revision: Union[str, Sequence[str], None] = '13d50945cf19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "users" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "onboarding_completed" not in columns:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.add_column(sa.Column("onboarding_completed", sa.Boolean(), server_default="0", nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "users" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "onboarding_completed" in columns:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.drop_column("onboarding_completed")
