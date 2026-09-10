-- 072_d4_d6_rear_delt_split.sql — splits the D4/D6 duplicate "Better Fly
-- Rear Delt Extension [FT]" into two distinct angles on the same Better Fly
-- cable stack (load_equipment_id=6), athlete directive 2026-09-10: cross-body
-- path -> D4 T3 GS (fresh slot d4_t3h), bent-over path -> D6 GS1 (fresh slot
-- d6_g2i). Both new movements needs-cal (zero prior history). Mirrors
-- ironlog/seed.py's committed dicts and rule_wiring.py's RPE_8_STANDARD rule
-- for both slots (unchanged from the movement they replace). Distinct from
-- the pre-existing unwired "Cross-Body Cable Rear Delt Fly [FT]" (id 49, no
-- BETTER_FLY tag) -- confirmed a different physical setup, not a rename.
-- "Better Fly Rear Delt Extension [FT]" stays ACTIVE, left unwired, per the
-- never-delete-orphans convention.

INSERT INTO movement (
    name, base_name, region, lift_category, is_primary, unilateral, is_tracked,
    status, load_equipment_id, equipment_tags, primary_muscle, secondary_muscles,
    progression_mode, scheme, increment_ladder, min_step, load_floor,
    rpe_capped, rpe_cap_exempt, is_family_anchor, band_eligible, ramp_eligible,
    progression_rule
)
SELECT
    'Better Fly Cross-Body Rear Delt Fly [FT]', 'Better Fly Cross-Body Rear Delt Fly', 'UPPER', 'NONE', 0, 1, 1,
    'ACTIVE', 6, '["FT", "BETTER_FLY"]', 'REAR_DELT', '[]',
    'LADDER', 'DOUBLE_PROGRESSION', '[2.5]', 2.5, 10.0,
    0, 0, 0, 0, 0,
    'RPE_8_STANDARD'
WHERE NOT EXISTS (SELECT 1 FROM movement WHERE name = 'Better Fly Cross-Body Rear Delt Fly [FT]');

INSERT INTO movement (
    name, base_name, region, lift_category, is_primary, unilateral, is_tracked,
    status, load_equipment_id, equipment_tags, primary_muscle, secondary_muscles,
    progression_mode, scheme, increment_ladder, min_step, load_floor,
    rpe_capped, rpe_cap_exempt, is_family_anchor, band_eligible, ramp_eligible,
    progression_rule
)
SELECT
    'Better Fly Rear Delt Raise [FT]', 'Better Fly Rear Delt Raise', 'UPPER', 'NONE', 0, 0, 1,
    'ACTIVE', 6, '["FT", "BETTER_FLY"]', 'REAR_DELT', '["SIDE_DELT"]',
    'LADDER', 'DOUBLE_PROGRESSION', '[2.5]', 2.5, 10.0,
    0, 0, 0, 0, 0,
    'RPE_8_STANDARD'
WHERE NOT EXISTS (SELECT 1 FROM movement WHERE name = 'Better Fly Rear Delt Raise [FT]');

UPDATE tierexercise SET
    movement_id = (SELECT id FROM movement WHERE name = 'Better Fly Cross-Body Rear Delt Fly [FT]'),
    slot_id = 'd4_t3h'
WHERE id = 21 AND slot_id = 'd4_t3f';

UPDATE tierexercise SET
    movement_id = (SELECT id FROM movement WHERE name = 'Better Fly Rear Delt Raise [FT]'),
    slot_id = 'd6_g2i'
WHERE id = 71 AND slot_id = 'd6_g2f';
