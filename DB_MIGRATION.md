# Database Migrations

This file keeps track of database migrations applied to the SQLite database.

## Migration Log

Format: `Date - Command - What was changed`

- 2026-06-02 - `CREATE TABLE pack_teams (pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE, PRIMARY KEY (pack_id, team_id)); ALTER TABLE packs ADD COLUMN creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL;` - Added pack_teams table for many-to-many relationship between packs and teams, and creator_id column to packs table to support private packs.
- 2026-06-03 - `ALTER TABLE logs ADD COLUMN severity VARCHAR(50); ALTER TABLE users ADD COLUMN notification_level VARCHAR(50) DEFAULT 'medium' NOT NULL;` - Added severity column to logs table and notification_level column to users table.
- 2026-06-03 - `ALTER TABLE users ADD COLUMN extended_edr_enabled BOOLEAN NOT NULL DEFAULT 0; ALTER TABLE logs ADD COLUMN triage_note TEXT; ALTER TABLE logs ADD COLUMN alert_resolution VARCHAR(50) NOT NULL DEFAULT 'unread';` - Added Extended EDR settings and triage fields to models.
- 2026-06-04 - `PRAGMA foreign_keys=OFF; CREATE TABLE logs_new (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL, time DATETIME NOT NULL, content TEXT NOT NULL, signature TEXT, severity VARCHAR(50), triage_note TEXT, alert_resolution VARCHAR(50) DEFAULT NULL, FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE); INSERT INTO logs_new (id, device_id, time, content, signature, severity, triage_note, alert_resolution) SELECT id, device_id, time, content, signature, severity, triage_note, CASE WHEN alert_resolution = 'unread' THEN NULL ELSE alert_resolution END FROM logs; DROP TABLE logs; ALTER TABLE logs_new RENAME TO logs; PRAGMA foreign_keys=ON;` - Made alert_resolution column in logs table nullable and set default to NULL.

