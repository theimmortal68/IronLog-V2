-- 048_seated_leg_extension_movement.sql — add the "Seated Leg Extension [GHR + FT]"
-- library movement (Hyper Pro GHR rig + Ares FT cable resistance, load_code=FT per
-- athlete confirmation), matching commit bbd54b0's ironlog/seed.py entry and
-- following the existing "Leg Extension [GHR]" (id 29) isolation-movement pattern
-- for defaults (is_primary=0, is_tracked=1, rpe_capped=0, scheme=DOUBLE_PROGRESSION),
-- combined-equipment load_equipment_id=8 (GHR) per the Belt Squat [GHR + FT] /
-- AbMat Ab Bench Pad Cable Crunch [FT] combo-equipment precedent.
-- Idempotent: guarded by name uniqueness (movement.name has a UNIQUE index).
INSERT INTO movement (
    name, base_name, region, lift_category, is_primary, unilateral, is_tracked,
    status, load_equipment_id, equipment_tags, primary_muscle, secondary_muscles,
    progression_mode, scheme, increment_ladder, min_step, load_floor,
    rpe_capped, rpe_cap_exempt, is_family_anchor, band_eligible, ramp_eligible,
    progression_rule
)
SELECT
    'Seated Leg Extension [GHR + FT]', 'Seated Leg Extension', 'LOWER', 'NONE', 0, 0, 1,
    'ACTIVE', 8, '["GHR", "FT"]', 'QUADS', '[]',
    'LADDER', 'DOUBLE_PROGRESSION', '[5, 2.5]', 2.5, 10.0,
    0, 0, 0, 0, 0,
    'RPE_8_STANDARD'
WHERE NOT EXISTS (SELECT 1 FROM movement WHERE name = 'Seated Leg Extension [GHR + FT]');
