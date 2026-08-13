-- 040_planned_set_skip_and_exercise_slot.sql — mid-workout swap/skip support.
-- is_skipped: a not-yet-logged PlannedSet the athlete chose to skip mid-session
-- (no SetLog is ever written for it). tier_exercise_id: the program slot that
-- generated this PlannedExercise, persisted so a "make permanent" swap can
-- attach a SlotMovementOverride to the right slot (previously this link
-- existed only in-memory during generation and was discarded — see
-- assembler.py's _build_exercise tier_exercise_id parameter). Both additive,
-- nullable/defaulted so existing rows are unaffected. ADD COLUMN is atomic
-- in SQLite.
ALTER TABLE plannedset ADD COLUMN is_skipped BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE plannedexercise ADD COLUMN tier_exercise_id INTEGER REFERENCES tierexercise(id);
