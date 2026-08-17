-- Initial schema for the personal diary bot.
-- Applied when PRAGMA user_version is below 1.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    registered_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_activity_at TEXT,
    balance REAL NOT NULL DEFAULT 0,
    daily_price REAL NOT NULL DEFAULT 0,
    paid_until_date TEXT,
    last_charge_date TEXT,
    deleted_at TEXT,
    bot_blocked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    telegram_id INTEGER PRIMARY KEY,
    reminders_enabled INTEGER NOT NULL DEFAULT 1,
    default_sleep_time TEXT NOT NULL DEFAULT '23:00',
    stats_prefs_json TEXT,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS balance_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    operation_type TEXT NOT NULL,
    balance_before REAL NOT NULL,
    balance_after REAL NOT NULL,
    created_at TEXT NOT NULL,
    comment TEXT,
    performed_by INTEGER,
    idempotency_key TEXT,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_balance_ops_idempotency
    ON balance_operations(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_balance_ops_user_time
    ON balance_operations(telegram_id, created_at);

CREATE TABLE IF NOT EXISTS cigarettes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_cigarettes_user_time
    ON cigarettes(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS sleep_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    bedtime TEXT,
    wake_time TEXT,
    duration_minutes INTEGER,
    quality INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_sleep_user_time
    ON sleep_records(telegram_id, bedtime, wake_time);

CREATE TABLE IF NOT EXISTS mood_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_mood_user_time
    ON mood_records(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS wellbeing_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    comment TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_wellbeing_user_time
    ON wellbeing_records(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS caffeine_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    drink_type TEXT NOT NULL,
    amount REAL,
    unit TEXT,
    extra_json TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_caffeine_user_time
    ON caffeine_records(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS alcohol_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    drink_type TEXT NOT NULL,
    amount REAL,
    unit TEXT,
    extra_json TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_alcohol_user_time
    ON alcohol_records(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS activity_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    activity_type TEXT NOT NULL,
    duration_minutes INTEGER,
    comment TEXT,
    extra_json TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_user_time
    ON activity_records(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_notes_user_time
    ON notes(telegram_id, occurred_at);

CREATE TABLE IF NOT EXISTS custom_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    unit TEXT,
    choices_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_custom_metrics_user
    ON custom_metrics(telegram_id, enabled);

CREATE TABLE IF NOT EXISTS custom_metric_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    metric_id INTEGER NOT NULL,
    value_number REAL,
    value_text TEXT,
    value_bool INTEGER,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
    FOREIGN KEY (metric_id) REFERENCES custom_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_custom_values_user_time
    ON custom_metric_values(telegram_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_custom_values_metric
    ON custom_metric_values(metric_id, occurred_at);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    reminder_type TEXT NOT NULL DEFAULT 'day_review',
    next_run_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sent_at TEXT,
    last_sent_local_date TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_reminders_due
    ON reminders(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS deleted_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    deleted_at TEXT NOT NULL,
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_deleted_accounts_user
    ON deleted_accounts(telegram_id, deleted_at);

CREATE TABLE IF NOT EXISTS processed_callbacks (
    callback_id TEXT PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_processed_callbacks_time
    ON processed_callbacks(processed_at);

CREATE TABLE IF NOT EXISTS system_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
