-- 054_d2_d4_core_double_progression.sql — outside-review reconciliation
-- (athlete directive, 2026-09-01): D2 Ab Trainer Decline Sit-up and D4 Ab
-- Trainer Hanging Leg Raise both display TierExercise.scheme=NULL (reverted
-- there by migration 045, since the outside review's original assumption --
-- no progression scheme existed -- was wrong: both movements already
-- progress live via progression_rule=INCLINE_REDUCTION on incline angle).
-- Re-reviewing the full program, the athlete wants the display field to
-- read DOUBLE_PROGRESSION (matching the actual incline-angle progression
-- mechanism) rather than blank. TierExercise.scheme is display-only
-- (generation/context.py:405 is its sole read site) -- zero behavioral
-- change, INCLINE_REDUCTION still drives the real progression math.
UPDATE tierexercise SET scheme = 'DOUBLE_PROGRESSION' WHERE id = 52;
UPDATE tierexercise SET scheme = 'DOUBLE_PROGRESSION' WHERE id = 55;
