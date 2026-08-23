-- Remove standalone mood, wellbeing and notes tracking.
-- Sleep quality stays on sleep_records.

DROP TABLE IF EXISTS mood_records;
DROP TABLE IF EXISTS wellbeing_records;
DROP TABLE IF EXISTS notes;
