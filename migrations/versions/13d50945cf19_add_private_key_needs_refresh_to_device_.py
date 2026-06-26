"""add_private_key_needs_refresh_to_device_groups

Revision ID: 13d50945cf19
Revises: 2add51ccb405
Create Date: 2026-06-26 06:56:37.437967

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '13d50945cf19'
down_revision: Union[str, Sequence[str], None] = '2add51ccb405'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('device_groups')]
    if 'private_key_needs_refresh' not in columns:
        with op.batch_alter_table('device_groups', schema=None) as batch_op:
            batch_op.add_column(sa.Column('private_key_needs_refresh', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('device_groups')]
    if 'private_key_needs_refresh' in columns:
        with op.batch_alter_table('device_groups', schema=None) as batch_op:
            batch_op.drop_column('private_key_needs_refresh')
