-- Good decisions: a date schedule on a custom metric.
-- Applied when PRAGMA user_version is below 18.

PRAGMA foreign_keys = ON;

ALTER TABLE custom_metrics ADD COLUMN starts_on TEXT;
ALTER TABLE custom_metrics ADD COLUMN ends_on TEXT;
ALTER TABLE custom_metrics ADD COLUMN weekdays INTEGER NOT NULL DEFAULT 127;
