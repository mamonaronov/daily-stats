-- Days a user chose not to rate, so they leave the unrated list.
-- Applied when PRAGMA user_version is below 20.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS daily_score_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
    CHECK (kind IN ('wellbeing', 'energy', 'productivity', 'mood', 'day_rating', 'stress')),
    UNIQUE (telegram_id, day, kind)
);
