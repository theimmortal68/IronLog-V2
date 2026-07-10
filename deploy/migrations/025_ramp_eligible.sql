-- 025_ramp_eligible.sql — heavy-barbell anchor warmup ramp eligibility flag.
-- Additive, non-null boolean with a false default so existing Movement rows
-- remain non-ramp-eligible until the seed explicitly marks the allowed anchors.
-- ADD COLUMN is atomic in SQLite. Type/default/nullability match Movement.ramp_eligible.
ALTER TABLE movement ADD COLUMN ramp_eligible BOOLEAN NOT NULL DEFAULT 0;
