-- 045_seated_ohp_barbell_and_scheme_correction.sql
--
-- Two athlete-driven corrections, 2026-08-31.

-- (1) D1's "seated OHP" accessory is performed with a barbell, not a
-- dumbbell -- the movement's [DB] tag/name is stale. Definitional fix only:
-- movement_id is unchanged so MovementState/e1RM/current_load history stays
-- attached; equipment_tags was already an empty JSON array (no functional
-- equipment link existed), and the movement's own load_floor/min_step/
-- increment_ladder columns (self-contained, not equipment-derived) are
-- untouched -- so this does not change any computed load. Rep scheme
-- (DOUBLE_PROGRESSION, seated) is unchanged, per the athlete's explicit
-- "programming stays the same" instruction. load_equipment_id set to match
-- the other two barbell OHP movements (Standing OHP id=5, Seated BTN OHP
-- id=128), both of which use equipment id 1.
UPDATE movement
SET name = 'Stryker Pad Seated OHP [PB]',
    equipment_tags = '["PB"]',
    load_equipment_id = 1
WHERE id = 121 AND name = 'Stryker Pad Seated OHP [DB]';

-- (2) Revert two TierExercise.scheme values wrongly set by migration 044.
-- Both Ab Trainer Hanging Leg Raise (movement 132) and Ab Trainer Decline
-- Sit-up (movement 127) already have real double-progression wired up at
-- the Movement level (progression_mode=ASSISTED, progression_rule=
-- INCLINE_REDUCTION, assist_unit=DEGREES, assist_ladder=[0,5,...85],
-- driven by ironlog/engine/advance.py's _incline_reduction) -- reps rebuild
-- 8-12 at the current incline, a clean 12 across all sets advances incline
-- one increment, reps reset toward 8. Migration 044 set
-- TierExercise.scheme (a display-only field -- grep-confirmed the only
-- read site is generation/context.py:405, never engine progression math)
-- to REP_LADDER/DOUBLE_PROGRESSION without checking this, which contradicts
-- the movement's real scheme (REP_RATIO) and mechanism. No behavioral
-- effect either way (scheme isn't read by progression math), but leaving
-- it wrong is misleading in any export/display. Revert to NULL, their
-- pre-044 state, matching every other INCLINE_REDUCTION slot in this
-- program (e.g. Ab Trainer Russian Twist's TierExercise row, untouched).
UPDATE tierexercise SET scheme = NULL WHERE id = 55;  -- Ab Trainer Hanging Leg Raise (D4)
UPDATE tierexercise SET scheme = NULL WHERE id = 52;  -- Ab Trainer Decline Sit-up (D2)
