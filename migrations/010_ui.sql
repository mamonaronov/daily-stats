-- UI prefs, pinned custom metrics, durable FSM storage.

ALTER TABLE user_settings ADD COLUMN ui_prefs_json TEXT;

ALTER TABLE custom_metrics ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS fsm_storage (
    bot_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    destiny TEXT NOT NULL DEFAULT 'default',
    state TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (bot_id, chat_id, user_id, destiny)
);
