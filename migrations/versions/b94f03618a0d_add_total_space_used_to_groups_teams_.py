"""add_total_space_used_to_groups_teams_users

Revision ID: b94f03618a0d
Revises: 906c47d894f0
Create Date: 2026-09-27 10:20:59.822673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b94f03618a0d'
down_revision: Union[str, Sequence[str], None] = '906c47d894f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "device_groups" in tables:
        columns = [c["name"] for c in inspector.get_columns("device_groups")]
        if "total_space_used" not in columns:
            with op.batch_alter_table("device_groups", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "total_space_used",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    )
                )

    if "teams" in tables:
        columns = [c["name"] for c in inspector.get_columns("teams")]
        if "total_space_used" not in columns:
            with op.batch_alter_table("teams", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "total_space_used",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    )
                )

    if "users" in tables:
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "total_space_used" not in columns:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "total_space_used",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    )
                )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "users" in tables:
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "total_space_used" in columns:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.drop_column("total_space_used")

    if "teams" in tables:
        columns = [c["name"] for c in inspector.get_columns("teams")]
        if "total_space_used" in columns:
            with op.batch_alter_table("teams", schema=None) as batch_op:
                batch_op.drop_column("total_space_used")

    if "device_groups" in tables:
        columns = [c["name"] for c in inspector.get_columns("device_groups")]
        if "total_space_used" in columns:
            with op.batch_alter_table("device_groups", schema=None) as batch_op:
                batch_op.drop_column("total_space_used")
