-- Instant "fooling around" events, same shape as cigarettes.
-- Applied when PRAGMA user_version is below 4.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS fooling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_fooling_user_time
    ON fooling(telegram_id, occurred_at);
