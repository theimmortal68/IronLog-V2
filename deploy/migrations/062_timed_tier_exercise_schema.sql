-- 062_timed_tier_exercise_schema.sql — schema only (spec 59): additive
-- nullable duration columns paralleling the existing rep columns, so a
-- TierExercise/PlannedSet/SetLog can carry a duration-based prescription
-- (seconds per set) instead of reps -- a TierExercise is expected to have
-- either rep fields OR duration fields populated, never both (convention,
-- not a DB constraint, same as this repo's other either/or shapes).
-- All-additive schema changes may be multi-statement per this file's own
-- authoring rule (README.md) -- no data statements here, split into 063.
ALTER TABLE tierexercise ADD COLUMN duration_low_seconds INTEGER;
ALTER TABLE tierexercise ADD COLUMN duration_high_seconds INTEGER;
ALTER TABLE plannedset ADD COLUMN target_duration_low_seconds INTEGER;
ALTER TABLE plannedset ADD COLUMN target_duration_high_seconds INTEGER;
ALTER TABLE setlog ADD COLUMN actual_duration_seconds INTEGER;
