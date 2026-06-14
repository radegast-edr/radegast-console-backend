"""initial_schema

Revision ID: 6456f51f7b88
Revises: 
Create Date: 2026-06-14 15:28:35.357973

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6456f51f7b88'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Helper function to check column existence in existing table
    def column_exists(table_name: str, column_name: str) -> bool:
        if table_name not in existing_tables:
            return False
        cols = inspector.get_columns(table_name)
        return any(c['name'] == column_name for c in cols)

    # Helper function to check index existence
    def index_exists(table_name: str, index_name: str) -> bool:
        if table_name not in existing_tables:
            return False
        indexes = inspector.get_indexes(table_name)
        return any(idx['name'] == index_name for idx in indexes)

    # 1. device_groups
    if 'device_groups' not in existing_tables:
        op.create_table('device_groups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )

    # 2. devices
    if 'devices' not in existing_tables:
        op.create_table('devices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('token_change', sa.DateTime(timezone=True), nullable=True),
        sa.Column('signature_public_key', sa.Text(), nullable=True),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
        sa.Column('agent_version', sa.String(length=255), nullable=True),
        sa.Column('rustinel_version', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    else:
        if not column_exists('devices', 'agent_version'):
            op.add_column('devices', sa.Column('agent_version', sa.String(length=255), nullable=True))
        if not column_exists('devices', 'rustinel_version'):
            op.add_column('devices', sa.Column('rustinel_version', sa.String(length=255), nullable=True))

    # 3. email_bulk_states
    if 'email_bulk_states' not in existing_tables:
        op.create_table('email_bulk_states',
        sa.Column('email_to', sa.String(length=255), nullable=False),
        sa.Column('email_type', sa.String(length=50), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('email_to', 'email_type')
        )

    # 4. queued_emails
    if 'queued_emails' not in existing_tables:
        op.create_table('queued_emails',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email_to', sa.String(length=255), nullable=False),
        sa.Column('email_type', sa.String(length=50), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('html_body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )

    # 5. teams
    if 'teams' not in existing_tables:
        op.create_table('teams',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('permission_pack', sa.Enum('read', 'write', name='permissionpack'), nullable=True),
        sa.Column('permission_invite', sa.Enum('write', name='permissioninvite'), nullable=True),
        sa.Column('permission_admin', sa.Enum('write', name='permissionadmin'), nullable=True),
        sa.Column('permission_logs', sa.Enum('read', name='permissionlogs'), nullable=True),
        sa.Column('managing_team_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['managing_team_id'], ['teams.id'], ),
        sa.PrimaryKeyConstraint('id')
        )

    # 6. users
    if 'users' not in existing_tables:
        op.create_table('users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password', sa.String(length=255), nullable=False),
        sa.Column('role', sa.Enum('user', 'maintainer', 'admin', name='userrole'), nullable=False),
        sa.Column('password_change', sa.DateTime(timezone=True), nullable=False),
        sa.Column('registered_on', sa.DateTime(timezone=True), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False),
        sa.Column('otp_secret', sa.String(length=255), nullable=True),
        sa.Column('otp_enabled', sa.Boolean(), nullable=False),
        sa.Column('notify_login', sa.Boolean(), nullable=False),
        sa.Column('notify_new_keys', sa.Boolean(), nullable=False),
        sa.Column('notify_recovery_used', sa.Boolean(), nullable=False),
        sa.Column('notify_keys_transferred', sa.Boolean(), nullable=False),
        sa.Column('notify_device_log', sa.Boolean(), nullable=False),
        sa.Column('notify_downtime_maintenance', sa.Boolean(), nullable=False),
        sa.Column('notify_api_key_modification', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('notify_news_updates', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('notification_level', sa.Enum('informational', 'low', 'medium', 'high', 'critical', name='logseverity'), server_default='medium', nullable=False),
        sa.Column('extended_edr_enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('api_keys_enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
        )
    else:
        if not column_exists('users', 'notify_api_key_modification'):
            op.add_column('users', sa.Column('notify_api_key_modification', sa.Boolean(), server_default='1', nullable=False))
        if not column_exists('users', 'notify_news_updates'):
            op.add_column('users', sa.Column('notify_news_updates', sa.Boolean(), server_default='1', nullable=False))
        if not column_exists('users', 'notification_level'):
            op.add_column('users', sa.Column('notification_level', sa.Enum('informational', 'low', 'medium', 'high', 'critical', name='logseverity'), server_default='medium', nullable=False))
        if not column_exists('users', 'extended_edr_enabled'):
            op.add_column('users', sa.Column('extended_edr_enabled', sa.Boolean(), server_default='0', nullable=False))
        if not column_exists('users', 'api_keys_enabled'):
            op.add_column('users', sa.Column('api_keys_enabled', sa.Boolean(), server_default='0', nullable=False))

    # 7. api_keys
    if 'api_keys' not in existing_tables:
        op.create_table('api_keys',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('key_hash', sa.String(length=255), nullable=False),
        sa.Column('prefix', sa.String(length=16), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash')
        )

    # 8. device_group_devices
    if 'device_group_devices' not in existing_tables:
        op.create_table('device_group_devices',
        sa.Column('device_group_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['device_group_id'], ['device_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_group_id', 'device_id')
        )

    # 9. exclusions
    if 'exclusions' not in existing_tables:
        op.create_table('exclusions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_group_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('jsonata_query', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_group_id'], ['device_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )

    # 10. hardware_tokens
    if 'hardware_tokens' not in existing_tables:
        op.create_table('hardware_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.String(length=512), nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('credential_id')
        )

    # 11. key_transfers
    if 'key_transfers' not in existing_tables:
        op.create_table('key_transfers',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('receiver_age_public_key', sa.Text(), nullable=False),
        sa.Column('encrypted_private_key', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )

    # 12. logs
    if 'logs' not in existing_tables:
        op.create_table('logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.Column('severity', sa.Enum('informational', 'low', 'medium', 'high', 'critical', name='logseverity'), nullable=True),
        sa.Column('triage_note', sa.Text(), nullable=True),
        sa.Column('alert_resolution', sa.String(length=50), nullable=True),
        sa.Column('rule_id', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    else:
        if not column_exists('logs', 'severity'):
            op.add_column('logs', sa.Column('severity', sa.Enum('informational', 'low', 'medium', 'high', 'critical', name='logseverity'), nullable=True))
        if not column_exists('logs', 'triage_note'):
            op.add_column('logs', sa.Column('triage_note', sa.Text(), nullable=True))
        if not column_exists('logs', 'alert_resolution'):
            op.add_column('logs', sa.Column('alert_resolution', sa.String(length=50), nullable=True))
        if not column_exists('logs', 'rule_id'):
            op.add_column('logs', sa.Column('rule_id', sa.String(length=255), nullable=True))

    with op.batch_alter_table('logs', schema=None) as batch_op:
        if 'logs' not in existing_tables or not index_exists('logs', 'idx_logs_device_id_time'):
            batch_op.create_index('idx_logs_device_id_time', ['device_id', 'time'], unique=False)
        if 'logs' not in existing_tables or not index_exists('logs', 'idx_logs_time_severity'):
            batch_op.create_index('idx_logs_time_severity', ['time', 'severity'], unique=False)
        if 'logs' not in existing_tables or not index_exists('logs', 'ix_logs_alert_resolution'):
            batch_op.create_index(batch_op.f('ix_logs_alert_resolution'), ['alert_resolution'], unique=False)
        if 'logs' not in existing_tables or not index_exists('logs', 'ix_logs_device_id'):
            batch_op.create_index(batch_op.f('ix_logs_device_id'), ['device_id'], unique=False)
        if 'logs' not in existing_tables or not index_exists('logs', 'ix_logs_rule_id'):
            batch_op.create_index(batch_op.f('ix_logs_rule_id'), ['rule_id'], unique=False)
        if 'logs' not in existing_tables or not index_exists('logs', 'ix_logs_severity'):
            batch_op.create_index(batch_op.f('ix_logs_severity'), ['severity'], unique=False)
        if 'logs' not in existing_tables or not index_exists('logs', 'ix_logs_time'):
            batch_op.create_index(batch_op.f('ix_logs_time'), ['time'], unique=False)

    # 13. packs
    if 'packs' not in existing_tables:
        op.create_table('packs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pack_id', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('pack_id')
        )
    else:
        if not column_exists('packs', 'pack_id'):
            op.add_column('packs', sa.Column('pack_id', sa.String(length=255), nullable=False, server_default=''))
        if not column_exists('packs', 'creator_id'):
            op.add_column('packs', sa.Column('creator_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))

    # 14. public_keys
    if 'public_keys' not in existing_tables:
        op.create_table('public_keys',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('private_key', sa.Text(), nullable=True),
        sa.Column('key_type', sa.String(length=20), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )

    # 15. team_device_groups
    if 'team_device_groups' not in existing_tables:
        op.create_table('team_device_groups',
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('device_group_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['device_group_id'], ['device_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('team_id', 'device_group_id')
        )

    # 16. team_users
    if 'team_users' not in existing_tables:
        op.create_table('team_users',
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('team_id', 'user_id')
        )

    # 17. logs_seen
    if 'logs_seen' not in existing_tables:
        op.create_table('logs_seen',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('log_id', sa.Integer(), nullable=False),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['log_id'], ['logs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'log_id')
        )

    # 18. pack_teams
    if 'pack_teams' not in existing_tables:
        op.create_table('pack_teams',
        sa.Column('pack_id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['pack_id'], ['packs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('pack_id', 'team_id')
        )

    # 19. pack_versions
    if 'pack_versions' not in existing_tables:
        op.create_table('pack_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pack_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('zip_path', sa.String(length=512), nullable=False),
        sa.Column('released', sa.DateTime(timezone=True), nullable=False),
        sa.Column('release_notes', sa.String(length=1024), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['pack_id'], ['packs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    else:
        if not column_exists('pack_versions', 'meta'):
            op.add_column('pack_versions', sa.Column('meta', sa.JSON(), nullable=True))

    # 20. pack_enabled
    if 'pack_enabled' not in existing_tables:
        op.create_table('pack_enabled',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_group_id', sa.Integer(), nullable=False),
        sa.Column('pack_version_id', sa.Integer(), nullable=False),
        sa.Column('autoupdate', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['device_group_id'], ['device_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pack_version_id'], ['pack_versions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Drop in reverse order to respect foreign key constraints
    tables_to_drop = [
        'pack_enabled', 'pack_versions', 'pack_teams', 'logs_seen', 
        'team_users', 'team_device_groups', 'public_keys', 'packs',
        'logs', 'key_transfers', 'hardware_tokens', 'exclusions',
        'device_group_devices', 'api_keys', 'users', 'teams',
        'queued_emails', 'email_bulk_states', 'devices', 'device_groups'
    ]
    for table in tables_to_drop:
        if table in existing_tables:
            op.drop_table(table)
