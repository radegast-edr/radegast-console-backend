# Database Migrations

This file keeps track of database migrations applied to the SQLite database.

## Migration Log

Format: `Date - Command - What was changed`

- 2026-06-02 - `CREATE TABLE pack_teams (pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE, PRIMARY KEY (pack_id, team_id)); ALTER TABLE packs ADD COLUMN creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL;` - Added pack_teams table for many-to-many relationship between packs and teams, and creator_id column to packs table to support private packs.
- 2026-06-03 - `ALTER TABLE logs ADD COLUMN severity VARCHAR(50); ALTER TABLE users ADD COLUMN notification_level VARCHAR(50) DEFAULT 'medium' NOT NULL;` - Added severity column to logs table and notification_level column to users table.
