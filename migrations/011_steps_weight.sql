-- Built-in steps (one value per local day) and weight (timestamped weigh-ins).
-- Applied when PRAGMA user_version is below 11.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS step_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    steps INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_steps_user_day
    ON step_records(telegram_id, day);

CREATE INDEX IF NOT EXISTS idx_steps_user_time
    ON step_records(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS weight_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    kilograms REAL NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_weight_user_time
    ON weight_records(telegram_id, occurred_at);
