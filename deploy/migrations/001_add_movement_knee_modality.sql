-- 001_add_movement_knee_modality.sql
-- Adds movement.knee_modality (v0.3 cross-session knee-frequency classification).
-- Type VARCHAR(6) matches create_all's emitted type (NOT TEXT).
-- Nullable, no default — consistent with create_all (notnull=0, dflt_value=None).
ALTER TABLE movement ADD COLUMN knee_modality VARCHAR(6);
