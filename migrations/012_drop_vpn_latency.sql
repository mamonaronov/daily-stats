-- VPN latency samples live in a separate sqlite file and are not backed up.
-- Applied when PRAGMA user_version is below 12.

DROP TABLE IF EXISTS vpn_latency_samples;
