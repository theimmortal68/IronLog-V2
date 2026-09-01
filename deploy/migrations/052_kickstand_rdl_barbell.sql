-- 052_kickstand_rdl_barbell.sql — deploy commit 22a3f1a's already-committed
-- code (never applied live): Kickstand RDL was seeded as a unilateral
-- dumbbell movement, but the athlete actually trains it with a barbell.
-- New movement row "Kickstand RDL [PB]" (bilateral, load_floor=45 matching
-- Back Squat/RDL) added rather than mutating "Kickstand RDL [DB]" (id 133)
-- in place, matching this program's precedent for anchor swaps -- old row
-- stays ACTIVE/unwired. increment_ladder=[10,5] (narrower than other
-- lower-body T1 primaries' [10,5,2.5], per athlete directive) and
-- ramp_eligible=1 (was never set on the DB-era row, so it never generated
-- warmup sets -- same recurring-omission class as the prior Seated BTN OHP
-- incident). Mirrors ironlog/seed.py's committed dict exactly.
INSERT INTO movement (
    name, base_name, region, lift_category, is_primary, unilateral, is_tracked,
    status, load_equipment_id, equipment_tags, primary_muscle, secondary_muscles,
    progression_mode, scheme, increment_ladder, min_step, load_floor,
    rpe_capped, rpe_cap_exempt, is_family_anchor, band_eligible, ramp_eligible,
    progression_rule
)
SELECT
    'Kickstand RDL [PB]', 'Kickstand RDL', 'LOWER', 'NONE', 0, 0, 1,
    'ACTIVE', 1, '["PB"]', 'HAMSTRINGS', '["GLUTES", "SPINAL_ERECTORS"]',
    'LADDER', 'DOUBLE_PROGRESSION', '[10, 5]', 2.5, 45.0,
    0, 0, 0, 0, 1,
    'RPE_8_STANDARD'
WHERE NOT EXISTS (SELECT 1 FROM movement WHERE name = 'Kickstand RDL [PB]');

UPDATE tierexercise SET movement_id = (SELECT id FROM movement WHERE name = 'Kickstand RDL [PB]')
WHERE id = 58 AND movement_id = 133;
