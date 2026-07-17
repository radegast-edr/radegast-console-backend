"""add_response_settings_to_device_group

Revision ID: 3ff19315747c
Revises: d0aa0eab0b0a
Create Date: 2026-07-13 19:54:05.390005

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ff19315747c'
down_revision: Union[str, Sequence[str], None] = 'd0aa0eab0b0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("device_groups")]

    with op.batch_alter_table('device_groups', schema=None) as batch_op:
        if "response_enabled" not in columns:
            batch_op.add_column(sa.Column('response_enabled', sa.Boolean(), server_default='0', nullable=False))
        if "response_min_severity" not in columns:
            batch_op.add_column(sa.Column('response_min_severity', sa.String(length=50), server_default='critical', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("device_groups")]

    with op.batch_alter_table('device_groups', schema=None) as batch_op:
        if "response_min_severity" in columns:
            batch_op.drop_column('response_min_severity')
        if "response_enabled" in columns:
            batch_op.drop_column('response_enabled')
