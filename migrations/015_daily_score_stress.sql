-- Allow stress as a sixth daily 1–5 score kind.
-- Applied when PRAGMA user_version is below 15.
-- SQLite cannot ALTER a CHECK constraint, so the table is rebuilt.

PRAGMA foreign_keys = OFF;

CREATE TABLE daily_scores_new (
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
    CHECK (kind IN ('wellbeing', 'energy', 'productivity', 'mood', 'day_rating', 'stress'))
);

INSERT INTO daily_scores_new (
    id, telegram_id, day, kind, score, occurred_at, created_at, updated_at
)
SELECT id, telegram_id, day, kind, score, occurred_at, created_at, updated_at
FROM daily_scores;

DROP TABLE daily_scores;
ALTER TABLE daily_scores_new RENAME TO daily_scores;

CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_scores_user_day_kind
    ON daily_scores(telegram_id, day, kind);

CREATE INDEX IF NOT EXISTS idx_daily_scores_user_time
    ON daily_scores(telegram_id, occurred_at);

PRAGMA foreign_keys = ON;
