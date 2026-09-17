-- How the user woke: by themselves or because of something else.
-- Applied when PRAGMA user_version is below 17.

PRAGMA foreign_keys = ON;

ALTER TABLE sleep_records ADD COLUMN wake_kind TEXT;
