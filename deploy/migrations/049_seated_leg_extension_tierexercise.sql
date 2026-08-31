-- 049_seated_leg_extension_tierexercise.sql — wire the new "Seated Leg Extension
-- [GHR + FT]" movement (048) into D6's GS3 giant set at fresh slot d6_g3f,
-- exercise_order 4 (after Face Pull/Better Fly OH Tricep Extension/AbMat Ab Bench
-- Pad Cable Crunch), matching commit bbd54b0's program_seed.py wiring. GS3's live
-- tier id is 18 (program_day_id=6, tier_label='GS3' — confirmed against the live
-- DB directly, not assumed from seed code, since migration 044 already shifted
-- D6's tier_order once).
-- Idempotent: guarded by slot_id uniqueness within the tier (no unique constraint
-- exists on slot_id, so guard explicitly via WHERE NOT EXISTS).
INSERT INTO tierexercise (tier_id, slot_id, movement_id, exercise_order, tier_role, pattern, rep_low, rep_high, scheme)
SELECT
    18, 'd6_g3f', (SELECT id FROM movement WHERE name = 'Seated Leg Extension [GHR + FT]'),
    4, 'free', 'leg_extension', 10, 15, 'DOUBLE_PROGRESSION'
WHERE NOT EXISTS (SELECT 1 FROM tierexercise WHERE tier_id = 18 AND slot_id = 'd6_g3f');
