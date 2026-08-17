-- Snus can lifetime: bought_at → finished_at → duration_minutes.
-- Applied when PRAGMA user_version is below 2.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS snus_packs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    bought_at TEXT,
    finished_at TEXT,
    duration_minutes INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_snus_packs_user_time
    ON snus_packs(telegram_id, bought_at, finished_at);
