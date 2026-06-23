"""add_exclusion_type_and_excluded_by

Revision ID: f0736504326d
Revises: 5f543328be0e
Create Date: 2026-06-22 17:17:40.155491

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0736504326d'
down_revision: Union[str, Sequence[str], None] = '5f543328be0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    if 'exclusions' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('exclusions')]
        if 'exclusion_type' not in columns:
            with op.batch_alter_table('exclusions', schema=None) as batch_op:
                batch_op.add_column(sa.Column('exclusion_type', sa.String(length=20), server_default='hard', nullable=False))

    if 'logs' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('logs')]
        if 'excluded_by' not in columns:
            with op.batch_alter_table('logs', schema=None) as batch_op:
                batch_op.add_column(sa.Column('excluded_by', sa.Integer(), nullable=True))
                batch_op.create_foreign_key('fk_logs_excluded_by_exclusions', 'exclusions', ['excluded_by'], ['id'], ondelete='SET NULL')
            # Check if index already exists before creating
            indexes = [idx['name'] for idx in inspector.get_indexes('logs')]
            if 'ix_logs_excluded_by' not in indexes:
                op.create_index('ix_logs_excluded_by', 'logs', ['excluded_by'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    if 'logs' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('logs')]
        indexes = [idx['name'] for idx in inspector.get_indexes('logs')]
        if 'ix_logs_excluded_by' in indexes:
            op.drop_index('ix_logs_excluded_by', table_name='logs')
        if 'excluded_by' in columns:
            with op.batch_alter_table('logs', schema=None) as batch_op:
                batch_op.drop_column('excluded_by')
                
    if 'exclusions' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('exclusions')]
        if 'exclusion_type' in columns:
            with op.batch_alter_table('exclusions', schema=None) as batch_op:
                batch_op.drop_column('exclusion_type')
