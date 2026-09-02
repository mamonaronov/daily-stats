-- Daily 1–5 scores: wellbeing, energy, productivity, mood, day rating.
-- One value per kind per local day, upsert anytime (same idea as steps).
-- Applied when PRAGMA user_version is below 13.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS daily_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    score INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
    CHECK (score >= 1 AND score <= 5),
    CHECK (kind IN ('wellbeing', 'energy', 'productivity', 'mood', 'day_rating'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_scores_user_day_kind
    ON daily_scores(telegram_id, day, kind);

CREATE INDEX IF NOT EXISTS idx_daily_scores_user_time
    ON daily_scores(telegram_id, occurred_at);
