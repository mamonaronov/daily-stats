-- Optional daily reminder to log getting out of bed.
-- Applied when PRAGMA user_version is below 14.

PRAGMA foreign_keys = ON;

ALTER TABLE user_settings ADD COLUMN wake_up_reminder_time TEXT;
ALTER TABLE user_settings ADD COLUMN wake_up_reminder_sent_on TEXT;
