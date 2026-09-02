-- 063_suitcase_dreadmill_carry_content.sql — content migration, depends on
-- 062 landing first (references the new duration columns). Adds "Suitcase
-- Dreadmill Carry" to D2's T3 GS as a 3rd member (fresh slot d2_t3f,
-- exercise_order=3, after ATG Split Squat=1 / Hybrid Board Tib Raise
-- [D2]=2 -- live tier_id=8, TierExercise ids 15/66 re-verified against the
-- live DB directly before writing this, not assumed from seed code).
--
-- Equipment: plate-loaded, one arm loaded at a time (not simultaneous
-- per-hand like Dumbbells (MX100)), so load_unit='LB' (a single loaded
-- figure per set) rather than LB_PER_HAND -- Movement.unilateral=True
-- already carries "one side at a time", not double-encoded in the unit.
-- load_floor NULL (plate-loaded arm, no fixed-bar minimum). min_step=5.0
-- per athlete-confirmed plate-loading increment.
--
-- Movement: LADDER/DOUBLE_PROGRESSION mirrors every other timed-set-capable
-- LADDER movement's shape, substituting duration for reps (per spec 59's
-- working hypothesis, confirmed viable this session: run_analysis.py's
-- hit-target check now branches on target_duration_high_seconds before
-- falling back to target_reps_high, so RPE_8_STANDARD generalizes with no
-- new ProgressionRule value needed). increment_ladder=[5]/min_step=5.0.
-- Movement-level load_floor=0, NOT NULL: tests/test_library_seed.py's
-- test_load_progression_has_increment_source requires a real load_floor
-- on every LADDER movement; matches this repo's established convention
-- for plate-loaded gear with no fixed minimum (belt-squat/reverse-hyper
-- both use floor 0). The equipment row's own load_floor stays NULL
-- (equipment-level "no fixed minimum" is a separate, already-precedented
-- pattern -- GMWD hip thrust's equipment row is NULL too; Movement-level
-- load fields are independent of load_equipment_id regardless, set both
-- explicitly per this session's 045_...sql finding). Athlete expects to
-- start loaded above 75lb -- left as a notes breadcrumb for the wizard's
-- first calibration, NOT pre-seeded into MovementState.current_load
-- (matches how D6 Standing OHP was deliberately left uncalibrated in
-- migration 044).
--
-- TierExercise: duration_low_seconds=20, duration_high_seconds=30 (the
-- athlete's stated 20-30 sec/side target), rep_low/rep_high left NULL.
-- Two-sided ("per side") prescription follows this program's existing
-- unilateral convention: ONE PlannedSet per round represents both sides
-- performed (matching how Kickstand RDL -- itself unilateral -- has never
-- emitted two PlannedSet rows per round), not a new per-side convention.
--
-- Idempotent: INSERTs guarded by name/slot existence checks.
INSERT INTO equipment (name, load_floor, min_step, load_unit, available_phase)
SELECT 'Dreadmill', NULL, 5.0, 'LB', 'P1'
WHERE NOT EXISTS (SELECT 1 FROM equipment WHERE name = 'Dreadmill');

INSERT INTO movement (
    name, base_name, region, lift_category, is_primary, unilateral, is_tracked,
    status, load_equipment_id, equipment_tags, primary_muscle, secondary_muscles,
    progression_mode, scheme, increment_ladder, min_step, load_floor,
    rpe_capped, rpe_cap_exempt, is_family_anchor, band_eligible, ramp_eligible,
    progression_rule, notes
)
SELECT
    'Suitcase Dreadmill Carry', 'Suitcase Dreadmill Carry', 'CORE', 'NONE', 0, 1, 1,
    'ACTIVE', (SELECT id FROM equipment WHERE name = 'Dreadmill'), '["DREADMILL"]',
    'ABS', '["SPINAL_ERECTORS", "FOREARMS"]',
    'LADDER', 'DOUBLE_PROGRESSION', '[5]', 5.0, 0,
    0, 0, 0, 0, 0,
    'RPE_8_STANDARD', 'Athlete expects to start loaded above 75 lb -- first-session calibration guidance only, not a seeded baseline.'
WHERE NOT EXISTS (SELECT 1 FROM movement WHERE name = 'Suitcase Dreadmill Carry');

INSERT INTO tierexercise (
    tier_id, slot_id, movement_id, exercise_order, tier_role,
    duration_low_seconds, duration_high_seconds, scheme
)
SELECT
    8, 'd2_t3f', (SELECT id FROM movement WHERE name = 'Suitcase Dreadmill Carry'),
    3, 'free', 20, 30, 'DOUBLE_PROGRESSION'
WHERE NOT EXISTS (SELECT 1 FROM tierexercise WHERE tier_id = 8 AND slot_id = 'd2_t3f');
