-- 051_dips_added_resistance_reclassification.sql — fix Dips [TOWER + TUBES]
-- (movement 98) misclassification: the athlete's real bands (Draper's
-- Strength, stacked/swapped) ADD resistance to bodyweight dips, they don't
-- assist. The 2026-08-16 ASSISTED/CABLE_LB model had the progression
-- direction backwards -- a "too easy" tap walked assist_level DOWN (per
-- ASSISTANCE_REDUCTION's higher-lb-is-easier convention), which for an
-- added-resistance movement should instead walk the working number UP.
-- Confirmed live: assist_level drifted 50 -> 40 -> 30 across three ON_TARGET
-- sessions, the exact wrong direction. Reverts to LADDER/DOUBLE_PROGRESSION/
-- RPE_8_STANDARD, the same shape this movement already carried from
-- 2026-08-12 to 2026-08-16 (increment_ladder=[5]/min_step=5/load_floor=10
-- already sit on the row unused, reactivated as-is). current_load seeded
-- from the athlete's real last-performed weight (2026-08-31 session, single
-- purple Draper's Strength band, 3x12 all ON_TARGET) rather than the
-- corrupted assist_level=30. Every statement is a fixed-value idempotent
-- UPDATE keyed on stable ids (README's multi-statement-data-migration rule).
UPDATE movement SET progression_mode = 'LADDER', scheme = 'DOUBLE_PROGRESSION', progression_rule = 'RPE_8_STANDARD' WHERE id = 98;
UPDATE tierexercise SET scheme = 'DOUBLE_PROGRESSION' WHERE id = 67;
UPDATE movementstate SET current_load = 40, assist_level = NULL, active_rule = 'RPE_8_STANDARD', current_increment_tier = 0 WHERE id = 30;
