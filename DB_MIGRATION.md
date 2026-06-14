# Database Migrations

This file keeps track of database migrations applied to the SQLite database.

## Migration Log

Format: `Date - Command - What was changed`

- 2026-06-02 - `CREATE TABLE pack_teams (pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE, PRIMARY KEY (pack_id, team_id)); ALTER TABLE packs ADD COLUMN creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL;` - Added pack_teams table for many-to-many relationship between packs and teams, and creator_id column to packs table to support private packs.
- 2026-06-03 - `ALTER TABLE logs ADD COLUMN severity VARCHAR(50); ALTER TABLE users ADD COLUMN notification_level VARCHAR(50) DEFAULT 'medium' NOT NULL;` - Added severity column to logs table and notification_level column to users table.
- 2026-06-03 - `ALTER TABLE users ADD COLUMN extended_edr_enabled BOOLEAN NOT NULL DEFAULT 0; ALTER TABLE logs ADD COLUMN triage_note TEXT; ALTER TABLE logs ADD COLUMN alert_resolution VARCHAR(50) NOT NULL DEFAULT 'unread';` - Added Extended EDR settings and triage fields to models.
- 2026-06-04 - `PRAGMA foreign_keys=OFF; CREATE TABLE logs_new (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL, time DATETIME NOT NULL, content TEXT NOT NULL, signature TEXT, severity VARCHAR(50), triage_note TEXT, alert_resolution VARCHAR(50) DEFAULT NULL, FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE); INSERT INTO logs_new (id, device_id, time, content, signature, severity, triage_note, alert_resolution) SELECT id, device_id, time, content, signature, severity, triage_note, CASE WHEN alert_resolution = 'unread' THEN NULL ELSE alert_resolution END FROM logs; DROP TABLE logs; ALTER TABLE logs_new RENAME TO logs; PRAGMA foreign_keys=ON;` - Made alert_resolution column in logs table nullable and set default to NULL.
- 2026-06-04 - `ALTER TABLE devices ADD COLUMN agent_version VARCHAR(255); ALTER TABLE devices ADD COLUMN rustinel_version VARCHAR(255);` - Added agent_version and rustinel_version columns to devices table.
- 2026-06-05 - `ALTER TABLE packs ADD COLUMN pack_id VARCHAR(255); ALTER TABLE users ADD COLUMN api_keys_enabled BOOLEAN NOT NULL DEFAULT 0; CREATE TABLE api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name VARCHAR(255) NOT NULL, key_hash VARCHAR(255) NOT NULL UNIQUE, prefix VARCHAR(16) NOT NULL, scopes TEXT NOT NULL, created_at DATETIME NOT NULL, expires_at DATETIME);` - Added pack_id to packs, api_keys_enabled to users, and created api_keys table.
- 2026-06-05 - `ALTER TABLE users ADD COLUMN notify_api_key_modification BOOLEAN NOT NULL DEFAULT 1;` - Added notify_api_key_modification column to users table.
- 2026-06-05 - `ALTER TABLE api_keys ADD COLUMN last_used DATETIME;` - Added last_used column to api_keys table to track programmatic usage.
- 2026-06-06 - `ALTER TABLE pack_versions ADD COLUMN meta JSON;` - Added meta JSON column to pack_versions table for storing pack.yml metadata.
- 2026-06-09 - `CREATE TABLE exclusions (id INTEGER PRIMARY KEY AUTOINCREMENT, device_group_id INTEGER NOT NULL REFERENCES device_groups(id) ON DELETE CASCADE, name VARCHAR(255) NOT NULL, description TEXT, jsonata_query TEXT NOT NULL, created_at DATETIME NOT NULL);` - Added exclusions table for device group exclusion rules.
- 2026-06-10 - `CREATE INDEX IF NOT EXISTS idx_logs_device_id ON logs(device_id); CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(time); CREATE INDEX IF NOT EXISTS idx_logs_severity ON logs(severity); CREATE INDEX IF NOT EXISTS idx_logs_alert_resolution ON logs(alert_resolution); CREATE INDEX IF NOT EXISTS idx_logs_device_id_time ON logs(device_id, time); CREATE INDEX IF NOT EXISTS idx_logs_time_severity ON logs(time, severity);` - Added performance indexes to logs table for frequently queried columns.
- 2026-06-14 - `ALTER TABLE logs ADD COLUMN rule_id VARCHAR(255); CREATE INDEX IF NOT EXISTS idx_logs_rule_id ON logs(rule_id);` - Added rule_id column to logs table for tracking detection rules.
- 2026-06-14 - `ALTER TABLE users ADD COLUMN notify_news_updates BOOLEAN NOT NULL DEFAULT 1;` - Added notify_news_updates column to users table for platform news and updates preferences.




