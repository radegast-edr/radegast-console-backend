"""add_scheduled_at_to_queued_emails

Revision ID: 1f680449869a
Revises: 6456f51f7b88
Create Date: 2026-06-15 06:59:41.467491

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f680449869a'
down_revision: Union[str, Sequence[str], None] = '6456f51f7b88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('queued_emails')]

    if 'scheduled_at' not in columns:
        with op.batch_alter_table('queued_emails', schema=None) as batch_op:
            batch_op.add_column(sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('queued_emails')]

    if 'scheduled_at' in columns:
        with op.batch_alter_table('queued_emails', schema=None) as batch_op:
            batch_op.drop_column('scheduled_at')
