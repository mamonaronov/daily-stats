-- Walk and run store a finish time separately from the start.
-- Applied when PRAGMA user_version is below 19.

PRAGMA foreign_keys = ON;

ALTER TABLE activity_records ADD COLUMN ended_at TEXT;
