-- Host uptime at each VPN sample, to tell power-off from a crashed bot.
-- Applied when PRAGMA user_version is below 5.

ALTER TABLE vpn_latency_samples ADD COLUMN host_uptime_s REAL;
