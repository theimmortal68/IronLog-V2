-- 050_seated_leg_extension_move_to_gs1.sql — move "Seated Leg Extension [GHR + FT]"
-- (tierexercise id 78, D6) from GS3 (tier 18) to GS1 (tier 16), filling the
-- order-2 gap left when Swiss Bar CG Press was pulled out of GS1 (migration 044,
-- superseded by the new standalone Standing OHP T1 tier) -- restores 3 exercises
-- per giant set across GS1/GS2/GS3 (athlete directive, 2026-08-31). Fresh slot
-- "d6_g1g" per the never-reassign-slot_id convention (d6_g1b/c/d/f already
-- vacated in this program's history, grep-confirmed against full git log).
-- Single UPDATE statement, naturally idempotent: a second run's WHERE clause no
-- longer matches (tier_id/slot_id already changed), so it's a no-op.
UPDATE tierexercise SET tier_id = 16, slot_id = 'd6_g1g', exercise_order = 2
WHERE id = 78 AND tier_id = 18 AND slot_id = 'd6_g3f';
