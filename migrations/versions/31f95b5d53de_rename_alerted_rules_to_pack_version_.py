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
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Rename alerted_rules to pack_version_rules if alerted_rules exists and target doesn't
    if 'alerted_rules' in existing_tables and 'pack_version_rules' not in existing_tables:
        op.rename_table('alerted_rules', 'pack_version_rules')
    
    # Re-inspect to get updated table names
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'pack_version_rules' in existing_tables:
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('pack_version_rules')]
        with op.batch_alter_table('pack_version_rules', schema=None) as batch_op:
            if 'idx_pack_version_rules_pack_version_id' not in existing_indexes:
                batch_op.create_index('idx_pack_version_rules_pack_version_id', ['pack_version_id'], unique=False)
            if 'idx_pack_version_rules_rule_id' not in existing_indexes:
                batch_op.create_index('idx_pack_version_rules_rule_id', ['rule_id'], unique=False)
            if 'idx_pack_version_rules_rule_type' not in existing_indexes:
                batch_op.create_index('idx_pack_version_rules_rule_type', ['rule_type'], unique=False)

            if 'idx_alerted_rules_pack_version_id' in existing_indexes:
                batch_op.drop_index('idx_alerted_rules_pack_version_id')
            if 'idx_alerted_rules_rule_id' in existing_indexes:
                batch_op.drop_index('idx_alerted_rules_rule_id')
            if 'idx_alerted_rules_rule_type' in existing_indexes:
                batch_op.drop_index('idx_alerted_rules_rule_type')

    if 'logs' in existing_tables:
        logs_columns = [c['name'] for c in inspector.get_columns('logs')]
        logs_indexes = [idx['name'] for idx in inspector.get_indexes('logs')]
        
        if 'alerted_rule_id' in logs_columns and 'pack_version_rule_id' not in logs_columns:
            try:
                with op.batch_alter_table('logs', schema=None) as batch_op:
                    batch_op.alter_column('alerted_rule_id', new_column_name='pack_version_rule_id', existing_type=sa.Integer())
                    batch_op.drop_constraint('fk_logs_alerted_rule_id', type_='foreignkey')
            except Exception:
                try:
                    with op.batch_alter_table('logs', schema=None) as batch_op:
                        batch_op.alter_column('alerted_rule_id', new_column_name='pack_version_rule_id', existing_type=sa.Integer())
                except Exception:
                    pass

        try:
            with op.batch_alter_table('logs', schema=None) as batch_op:
                if 'ix_logs_alerted_rule_id' in logs_indexes:
                    batch_op.drop_index('ix_logs_alerted_rule_id')
                if 'ix_logs_pack_version_rule_id' not in logs_indexes:
                    batch_op.create_index('ix_logs_pack_version_rule_id', ['pack_version_rule_id'], unique=False)
                batch_op.create_foreign_key(
                    'fk_logs_pack_version_rule_id',
                    'pack_version_rules',
                    ['pack_version_rule_id'],
                    ['id'],
                    ondelete='SET NULL'
                )
        except Exception:
            try:
                with op.batch_alter_table('logs', schema=None) as batch_op:
                    if 'ix_logs_alerted_rule_id' in logs_indexes:
                        batch_op.drop_index('ix_logs_alerted_rule_id')
                    if 'ix_logs_pack_version_rule_id' not in logs_indexes:
                        batch_op.create_index('ix_logs_pack_version_rule_id', ['pack_version_rule_id'], unique=False)
            except Exception:
                pass


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. Drop foreign key constraint on logs first
    if 'logs' in existing_tables:
        try:
            with op.batch_alter_table('logs', schema=None) as batch_op:
                batch_op.drop_constraint('fk_logs_pack_version_rule_id', type_='foreignkey')
        except Exception:
            pass
    
    # 2. Add old indexes back and drop new indexes
    if 'pack_version_rules' in existing_tables:
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('pack_version_rules')]
        with op.batch_alter_table('pack_version_rules', schema=None) as batch_op:
            if 'idx_alerted_rules_rule_type' not in existing_indexes:
                batch_op.create_index('idx_alerted_rules_rule_type', ['rule_type'], unique=False)
            if 'idx_alerted_rules_rule_id' not in existing_indexes:
                batch_op.create_index('idx_alerted_rules_rule_id', ['rule_id'], unique=False)
            if 'idx_alerted_rules_pack_version_id' not in existing_indexes:
                batch_op.create_index('idx_alerted_rules_pack_version_id', ['pack_version_id'], unique=False)

            if 'idx_pack_version_rules_rule_type' in existing_indexes:
                batch_op.drop_index('idx_pack_version_rules_rule_type')
            if 'idx_pack_version_rules_rule_id' in existing_indexes:
                batch_op.drop_index('idx_pack_version_rules_rule_id')
            if 'idx_pack_version_rules_pack_version_id' in existing_indexes:
                batch_op.drop_index('idx_pack_version_rules_pack_version_id')

    # 3. Rename table back to alerted_rules
    if 'pack_version_rules' in existing_tables and 'alerted_rules' not in existing_tables:
        op.rename_table('pack_version_rules', 'alerted_rules')

    # 4. Alter column and recreate foreign key pointing to newly renamed alerted_rules
    if 'logs' in existing_tables:
        inspector = sa.inspect(conn)
        logs_columns = [c['name'] for c in inspector.get_columns('logs')]
        logs_indexes = [idx['name'] for idx in inspector.get_indexes('logs')]
        
        try:
            with op.batch_alter_table('logs', schema=None) as batch_op:
                if 'pack_version_rule_id' in logs_columns and 'alerted_rule_id' not in logs_columns:
                    batch_op.alter_column('pack_version_rule_id', new_column_name='alerted_rule_id', existing_type=sa.Integer())
        except Exception:
            pass

        try:
            with op.batch_alter_table('logs', schema=None) as batch_op:
                if 'ix_logs_pack_version_rule_id' in logs_indexes:
                    batch_op.drop_index('ix_logs_pack_version_rule_id')
                if 'ix_logs_alerted_rule_id' not in logs_indexes:
                    batch_op.create_index('ix_logs_alerted_rule_id', ['alerted_rule_id'], unique=False)
                batch_op.create_foreign_key(
                    'fk_logs_alerted_rule_id',
                    'alerted_rules',
                    ['alerted_rule_id'],
                    ['id'],
                    ondelete='SET NULL'
                )
        except Exception:
            try:
                with op.batch_alter_table('logs', schema=None) as batch_op:
                    if 'ix_logs_pack_version_rule_id' in logs_indexes:
                        batch_op.drop_index('ix_logs_pack_version_rule_id')
                    if 'ix_logs_alerted_rule_id' not in logs_indexes:
                        batch_op.create_index('ix_logs_alerted_rule_id', ['alerted_rule_id'], unique=False)
            except Exception:
                pass

