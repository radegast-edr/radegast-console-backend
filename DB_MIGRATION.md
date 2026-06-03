# Database Migrations

This file keeps track of database migrations applied to the SQLite database.

## Migration Log

Format: `Date - Command - What was changed`

- 2026-06-02 - `CREATE TABLE pack_teams (pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE, PRIMARY KEY (pack_id, team_id)); ALTER TABLE packs ADD COLUMN creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL;` - Added pack_teams table for many-to-many relationship between packs and teams, and creator_id column to packs table to support private packs.
