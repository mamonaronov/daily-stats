-- Optional daily reminder to rate the day.
-- Applied when PRAGMA user_version is below 16.

PRAGMA foreign_keys = ON;

ALTER TABLE user_settings ADD COLUMN daily_score_reminder_time TEXT;
ALTER TABLE user_settings ADD COLUMN daily_score_reminder_sent_on TEXT;
