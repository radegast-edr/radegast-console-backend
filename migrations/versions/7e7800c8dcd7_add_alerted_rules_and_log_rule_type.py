"""add_alerted_rules_and_log_rule_type

Revision ID: 7e7800c8dcd7
Revises: 1f680449869a
Create Date: 2026-06-18 07:48:58.942612

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e7800c8dcd7'
down_revision: Union[str, Sequence[str], None] = '1f680449869a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    def column_exists(table_name: str, column_name: str) -> bool:
        if table_name not in existing_tables:
            return False
        return any(c['name'] == column_name for c in inspector.get_columns(table_name))

    def index_exists(table_name: str, index_name: str) -> bool:
        if table_name not in existing_tables:
            return False
        return any(idx['name'] == index_name for idx in inspector.get_indexes(table_name))

    # 1. Create alerted_rules table
    if 'alerted_rules' not in existing_tables:
        op.create_table(
            'alerted_rules',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('rule_id', sa.String(length=512), nullable=False),
            sa.Column('rule_type', sa.String(length=20), nullable=False),
            sa.Column('pack_version_id', sa.Integer(), nullable=False),
            sa.Column('rule_content', sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(['pack_version_id'], ['pack_versions.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('rule_id', 'rule_type', 'pack_version_id', name='uq_alerted_rules_rule_pack'),
        )
        with op.batch_alter_table('alerted_rules', schema=None) as batch_op:
            batch_op.create_index('idx_alerted_rules_pack_version_id', ['pack_version_id'], unique=False)
            batch_op.create_index('idx_alerted_rules_rule_id', ['rule_id'], unique=False)
            batch_op.create_index('idx_alerted_rules_rule_type', ['rule_type'], unique=False)

    # 2. Add rule_type and alerted_rule_id columns to logs
    with op.batch_alter_table('logs', schema=None) as batch_op:
        if not column_exists('logs', 'rule_type'):
            batch_op.add_column(sa.Column('rule_type', sa.String(length=20), nullable=True))
        if not column_exists('logs', 'alerted_rule_id'):
            batch_op.add_column(sa.Column('alerted_rule_id', sa.Integer(), nullable=True))
        if not index_exists('logs', 'ix_logs_alerted_rule_id'):
            batch_op.create_index('ix_logs_alerted_rule_id', ['alerted_rule_id'], unique=False)
        # Foreign key is added unconditionally in batch mode; SQLite will re-create the table.
        # On other databases, skip if you have a mechanism to check FK existence.
        try:
            batch_op.create_foreign_key(
                'fk_logs_alerted_rule_id',
                'alerted_rules',
                ['alerted_rule_id'],
                ['id'],
                ondelete='SET NULL',
            )
        except Exception:
            pass


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    def column_exists(table_name: str, column_name: str) -> bool:
        if table_name not in existing_tables:
            return False
        return any(c['name'] == column_name for c in inspector.get_columns(table_name))

    def index_exists(table_name: str, index_name: str) -> bool:
        if table_name not in existing_tables:
            return False
        return any(idx['name'] == index_name for idx in inspector.get_indexes(table_name))

    # Remove alerted_rule_id and rule_type from logs
    with op.batch_alter_table('logs', schema=None) as batch_op:
        if index_exists('logs', 'ix_logs_alerted_rule_id'):
            batch_op.drop_index('ix_logs_alerted_rule_id')
        if column_exists('logs', 'alerted_rule_id'):
            batch_op.drop_column('alerted_rule_id')
        if column_exists('logs', 'rule_type'):
            batch_op.drop_column('rule_type')

    # Drop alerted_rules table
    if 'alerted_rules' in inspector.get_table_names():
        op.drop_table('alerted_rules')
