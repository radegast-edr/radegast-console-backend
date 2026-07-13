"""add_device_os

Revision ID: 35083de9af22
Revises: 31f95b5d53de
Create Date: 2026-06-18 14:41:18.423691

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '35083de9af22'
down_revision: Union[str, Sequence[str], None] = '31f95b5d53de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # 1. Add os to devices
    devices_columns = [c['name'] for c in inspector.get_columns('devices')]
    if 'os' not in devices_columns:
        with op.batch_alter_table('devices', schema=None) as batch_op:
            batch_op.add_column(sa.Column('os', sa.String(length=50), nullable=True))

    # 2. Update unique constraint on pack_version_rules
    if 'pack_version_rules' in inspector.get_table_names():
        uq_constraints = [uq['name'] for uq in inspector.get_unique_constraints('pack_version_rules')]
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('pack_version_rules')]
        
        with op.batch_alter_table('pack_version_rules', schema=None) as batch_op:
            if 'uq_alerted_rules_rule_pack' in uq_constraints or 'uq_alerted_rules_rule_pack' in existing_indexes:
                try:
                    batch_op.drop_constraint('uq_alerted_rules_rule_pack', type_='unique')
                except Exception:
                    try:
                        batch_op.drop_index('uq_alerted_rules_rule_pack')
                    except Exception:
                        pass
            
            if 'uq_pack_version_rules_rule_pack' not in uq_constraints and 'uq_pack_version_rules_rule_pack' not in existing_indexes:
                batch_op.create_unique_constraint('uq_pack_version_rules_rule_pack', ['rule_id', 'rule_type', 'pack_version_id'])


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'pack_version_rules' in existing_tables:
        uq_constraints = [uq['name'] for uq in inspector.get_unique_constraints('pack_version_rules')]
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('pack_version_rules')]
        
        with op.batch_alter_table('pack_version_rules', schema=None) as batch_op:
            if 'uq_pack_version_rules_rule_pack' in uq_constraints or 'uq_pack_version_rules_rule_pack' in existing_indexes:
                try:
                    batch_op.drop_constraint('uq_pack_version_rules_rule_pack', type_='unique')
                except Exception:
                    try:
                        batch_op.drop_index('uq_pack_version_rules_rule_pack')
                    except Exception:
                        pass
            
            if 'uq_alerted_rules_rule_pack' not in uq_constraints and 'uq_alerted_rules_rule_pack' not in existing_indexes:
                batch_op.create_unique_constraint('uq_alerted_rules_rule_pack', ['rule_id', 'rule_type', 'pack_version_id'])

    if 'devices' in existing_tables:
        devices_columns = [c['name'] for c in inspector.get_columns('devices')]
        if 'os' in devices_columns:
            with op.batch_alter_table('devices', schema=None) as batch_op:
                batch_op.drop_column('os')

