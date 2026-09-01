-- 058_d5_bss_straight_eight.sql — deploys the previously-withheld part of
-- commit 4a29f4d (already committed 2026-08-29, never applied live):
-- Matrix Machine Bulgarian Split Squat (D5 GS2) DOUBLE_PROGRESSION 8-12 ->
-- STRAIGHT fixed-8-rep, increment_ladder [5,2.5] -> [2.5]. Athlete directive
-- (2026-09-01, after reviewing the gist and confirming): higher reps on
-- this movement are too fatiguing -- single fixed-8 target, not a range.
UPDATE movement SET scheme = 'STRAIGHT', increment_ladder = '[2.5]' WHERE id = 148;
UPDATE tierexercise SET rep_low = 8, rep_high = 8, scheme = 'STRAIGHT' WHERE id = 75;
