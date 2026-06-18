"""rename alerted_rules to pack_version_rules

Revision ID: 31f95b5d53de
Revises: 7e7800c8dcd7
Create Date: 2026-06-18 14:32:46.409235

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31f95b5d53de'
down_revision: Union[str, Sequence[str], None] = '7e7800c8dcd7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.rename_table('alerted_rules', 'pack_version_rules')
    
    with op.batch_alter_table('pack_version_rules', schema=None) as batch_op:
        batch_op.drop_index('idx_alerted_rules_pack_version_id')
        batch_op.drop_index('idx_alerted_rules_rule_id')
        batch_op.drop_index('idx_alerted_rules_rule_type')
        batch_op.create_index('idx_pack_version_rules_pack_version_id', ['pack_version_id'], unique=False)
        batch_op.create_index('idx_pack_version_rules_rule_id', ['rule_id'], unique=False)
        batch_op.create_index('idx_pack_version_rules_rule_type', ['rule_type'], unique=False)

    with op.batch_alter_table('logs', schema=None) as batch_op:
        batch_op.alter_column('alerted_rule_id', new_column_name='pack_version_rule_id')
        batch_op.drop_constraint('fk_logs_alerted_rule_id', type_='foreignkey')

    with op.batch_alter_table('logs', schema=None) as batch_op:
        batch_op.drop_index('ix_logs_alerted_rule_id')
        batch_op.create_index('ix_logs_pack_version_rule_id', ['pack_version_rule_id'], unique=False)
        batch_op.create_foreign_key('fk_logs_pack_version_rule_id', 'pack_version_rules', ['pack_version_rule_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('logs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_logs_pack_version_rule_id', type_='foreignkey')
        batch_op.alter_column('pack_version_rule_id', new_column_name='alerted_rule_id')

    with op.batch_alter_table('logs', schema=None) as batch_op:
        batch_op.drop_index('ix_logs_pack_version_rule_id')
        batch_op.create_index('ix_logs_alerted_rule_id', ['alerted_rule_id'], unique=False)
        batch_op.create_foreign_key('fk_logs_alerted_rule_id', 'alerted_rules', ['alerted_rule_id'], ['id'], ondelete='SET NULL')
    
    with op.batch_alter_table('pack_version_rules', schema=None) as batch_op:
        batch_op.drop_index('idx_pack_version_rules_rule_type')
        batch_op.drop_index('idx_pack_version_rules_rule_id')
        batch_op.drop_index('idx_pack_version_rules_pack_version_id')
        batch_op.create_index('idx_alerted_rules_rule_type', ['rule_type'], unique=False)
        batch_op.create_index('idx_alerted_rules_rule_id', ['rule_id'], unique=False)
        batch_op.create_index('idx_alerted_rules_pack_version_id', ['pack_version_id'], unique=False)

    op.rename_table('pack_version_rules', 'alerted_rules')
