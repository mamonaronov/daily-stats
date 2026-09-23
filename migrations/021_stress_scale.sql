-- Stress used to share the "higher is better" scale, so 5 meant a calm day.
-- The scale now measures stress itself: 1 is calm, 5 is the strongest.
-- Invert stored values so old entries keep that meaning.

UPDATE daily_scores
SET score = 6 - score
WHERE kind = 'stress';
