-- Sleep stages: phone in bed, phone away, sleep onset, out of bed.
-- Existing bedtime is treated as "лёг без телефона".

ALTER TABLE sleep_records ADD COLUMN phone_in_bed_at TEXT;
ALTER TABLE sleep_records ADD COLUMN phone_away_at TEXT;
ALTER TABLE sleep_records ADD COLUMN sleep_onset_at TEXT;
ALTER TABLE sleep_records ADD COLUMN out_of_bed_at TEXT;

UPDATE sleep_records
SET phone_away_at = bedtime
WHERE bedtime IS NOT NULL AND phone_away_at IS NULL;
