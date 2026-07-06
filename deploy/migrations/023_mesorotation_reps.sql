-- 023_mesorotation_reps.sql — add nullable rep-range override columns to
-- mesorotation. None = inherit the base TierExercise reps. Task 3 sets these
-- for the D5 single-leg Scout Meso-2 rotation row. Additive ADD COLUMNs;
-- columns match SQLModel create_all output (parity test).
ALTER TABLE mesorotation ADD COLUMN rep_low INTEGER;
ALTER TABLE mesorotation ADD COLUMN rep_high INTEGER;
