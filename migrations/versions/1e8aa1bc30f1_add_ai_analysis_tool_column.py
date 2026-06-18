"""add_ai_analysis_tool_column

Revision ID: 1e8aa1bc30f1
Revises: 35083de9af22
Create Date: 2026-06-18 18:57:32.783001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e8aa1bc30f1'
down_revision: Union[str, Sequence[str], None] = '35083de9af22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('users')]
    if 'ai_analysis_tool' not in columns:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column('ai_analysis_tool', sa.String(length=50), server_default='lumo-guest', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('users')]
    if 'ai_analysis_tool' in columns:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.drop_column('ai_analysis_tool')
