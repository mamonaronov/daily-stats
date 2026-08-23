-- Remove daily "review the day" reminders.
-- Applied when PRAGMA user_version is below 6.

DROP TABLE IF EXISTS reminders;

ALTER TABLE user_settings DROP COLUMN reminders_enabled;
