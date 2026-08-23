-- Event markers (named timestamps) and optional periods linking two markers.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS event_markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    name TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_event_markers_user_time
    ON event_markers(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS event_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    start_marker_id INTEGER NOT NULL,
    end_marker_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
    FOREIGN KEY (start_marker_id) REFERENCES event_markers(id) ON DELETE CASCADE,
    FOREIGN KEY (end_marker_id) REFERENCES event_markers(id) ON DELETE SET NULL,
    CHECK (end_marker_id IS NULL OR start_marker_id != end_marker_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_event_periods_start
    ON event_periods(start_marker_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_event_periods_end
    ON event_periods(end_marker_id)
    WHERE end_marker_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_event_periods_user
    ON event_periods(telegram_id);
