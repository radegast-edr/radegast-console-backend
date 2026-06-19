"""add_alert_id_to_exclusions

Revision ID: 5f543328be0e
Revises: 1e8aa1bc30f1
Create Date: 2026-06-19 05:48:30.175948

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f543328be0e'
down_revision: Union[str, Sequence[str], None] = '1e8aa1bc30f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # Check if 'exclusions' table exists
    if 'exclusions' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('exclusions')]
        if 'alert_id' not in columns:
            with op.batch_alter_table('exclusions', schema=None) as batch_op:
                batch_op.add_column(sa.Column('alert_id', sa.Integer(), nullable=True))
                batch_op.create_foreign_key('fk_exclusions_alert_id_logs', 'logs', ['alert_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    if 'exclusions' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('exclusions')]
        if 'alert_id' in columns:
            with op.batch_alter_table('exclusions', schema=None) as batch_op:
                # For SQLite/batch, we may need to drop constraint by name, or drop_column handles it.
                # In batch mode, batch_op.drop_column handles FK cleanup.
                batch_op.drop_column('alert_id')
