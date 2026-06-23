"""add_encryption_public_key_to_devices

Revision ID: e588c10e0981
Revises: f0736504326d
Create Date: 2026-06-23 09:54:54.648359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e588c10e0981'
down_revision: Union[str, Sequence[str], None] = 'f0736504326d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('devices')]
    if 'encryption_public_key' not in columns:
        with op.batch_alter_table('devices', schema=None) as batch_op:
            batch_op.add_column(sa.Column('encryption_public_key', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('devices')]
    if 'encryption_public_key' in columns:
        with op.batch_alter_table('devices', schema=None) as batch_op:
            batch_op.drop_column('encryption_public_key')
