-- Bot API latency samples with the current mihomo AUTO node.
-- Applied when PRAGMA user_version is below 3.

CREATE TABLE IF NOT EXISTS vpn_latency_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    measured_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    node_name TEXT,
    subscription TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_vpn_latency_measured_at
    ON vpn_latency_samples(measured_at);

CREATE INDEX IF NOT EXISTS idx_vpn_latency_sub_time
    ON vpn_latency_samples(subscription, measured_at);
