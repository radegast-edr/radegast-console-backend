"""e2ee_exclusions

Revision ID: 2add51ccb405
Revises: e588c10e0981
Create Date: 2026-06-25 06:45:37.913517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2add51ccb405'
down_revision: Union[str, Sequence[str], None] = 'e588c10e0981'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Create team_invitations table if not exists
    if "team_invitations" not in tables:
        op.create_table(
            "team_invitations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id")
        )

    # 2. Add columns private_key and public_key to device_groups if they don't exist
    dg_columns = [col["name"] for col in inspector.get_columns("device_groups")]
    with op.batch_alter_table("device_groups", schema=None) as batch_op:
        if "private_key" not in dg_columns:
            batch_op.add_column(sa.Column("private_key", sa.Text(), nullable=True))
        if "public_key" not in dg_columns:
            batch_op.add_column(sa.Column("public_key", sa.Text(), nullable=True))

    # 3. Add column encrypted to exclusions if it doesn't exist
    exclusions_columns = [col["name"] for col in inspector.get_columns("exclusions")]
    if "encrypted" not in exclusions_columns:
        with op.batch_alter_table("exclusions", schema=None) as batch_op:
            batch_op.add_column(sa.Column("encrypted", sa.Boolean(), server_default="0", nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Drop encrypted from exclusions
    if "exclusions" in tables:
        exclusions_columns = [col["name"] for col in inspector.get_columns("exclusions")]
        if "encrypted" in exclusions_columns:
            with op.batch_alter_table("exclusions", schema=None) as batch_op:
                batch_op.drop_column("encrypted")

    # 2. Drop columns from device_groups
    if "device_groups" in tables:
        dg_columns = [col["name"] for col in inspector.get_columns("device_groups")]
        with op.batch_alter_table("device_groups", schema=None) as batch_op:
            if "public_key" in dg_columns:
                batch_op.drop_column("public_key")
            if "private_key" in dg_columns:
                batch_op.drop_column("private_key")

    # 3. Drop team_invitations table
    if "team_invitations" in tables:
        op.drop_table("team_invitations")
