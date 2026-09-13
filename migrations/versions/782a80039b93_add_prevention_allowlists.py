"""add_prevention_allowlists

Revision ID: 782a80039b93
Revises: 04cf64736a71
Create Date: 2026-09-08 17:50:46.877435

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '782a80039b93'
down_revision: Union[str, Sequence[str], None] = '04cf64736a71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('prevention_allowlists'):
        op.create_table('prevention_allowlists',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('device_group_id', sa.Integer(), nullable=False),
            sa.Column('entry_type', sa.String(length=20), nullable=False),
            sa.Column('value', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['device_group_id'], ['device_groups.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )

def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table('prevention_allowlists'):
        op.drop_table('prevention_allowlists')
