-- 057_d6_gs1_serratus_swap.sql — outside-review reconciliation (athlete
-- directive, 2026-09-01): replaces D6 GS1's Seated Leg Extension (wired
-- 2026-08-29/31, migrations 048-050) with "Cable Serratus Punch/Reach [FT]"
-- -- quads already get real direct work on D2 (Belt Squat/Sissy Squat/ATG
-- Split Squat) and D5 (Reverse Nordic/Bulgarian Split Squat), while the
-- review surfaced a real gap: heavy rowing/vertical-pull/rear-delt/
-- retraction volume with comparatively little direct serratus/scapular-
-- protraction work. Same equipment class/shape as Face Pull [FT] (single
-- Ares cable, load_equipment_id=6, LADDER/DOUBLE_PROGRESSION). Mirrors
-- ironlog/seed.py's committed dict. Fresh slot "d6_g1h" (never-reassign-
-- slot_id convention -- d6_g1b/c/d/f/g all previously vacated in this
-- program's history, grep-confirmed); d6_g1g stays vacated, not reused.
-- Seated Leg Extension [GHR + FT] stays ACTIVE, left unwired, per the
-- never-delete-orphans convention.
INSERT INTO movement (
    name, base_name, region, lift_category, is_primary, unilateral, is_tracked,
    status, load_equipment_id, equipment_tags, primary_muscle, secondary_muscles,
    progression_mode, scheme, increment_ladder, min_step, load_floor,
    rpe_capped, rpe_cap_exempt, is_family_anchor, band_eligible, ramp_eligible,
    progression_rule
)
SELECT
    'Cable Serratus Punch/Reach [FT]', 'Cable Serratus Punch/Reach', 'UPPER', 'NONE', 0, 0, 1,
    'ACTIVE', 6, '["FT"]', 'SERRATUS', '["FRONT_DELT"]',
    'LADDER', 'DOUBLE_PROGRESSION', '[2.5]', 2.5, 10.0,
    0, 0, 0, 0, 0,
    'RPE_8_STANDARD'
WHERE NOT EXISTS (SELECT 1 FROM movement WHERE name = 'Cable Serratus Punch/Reach [FT]');

UPDATE tierexercise SET
    movement_id = (SELECT id FROM movement WHERE name = 'Cable Serratus Punch/Reach [FT]'),
    slot_id = 'd6_g1h', pattern = 'serratus_protraction', rep_low = 12, rep_high = 20
WHERE id = 78 AND slot_id = 'd6_g1g';
