-- 012_add_movement_unilateral.sql
-- Add Movement.unilateral (per-side movement flag). Additive, nullable-safe:
-- NOT NULL with DEFAULT 0 backfills existing rows to False.

ALTER TABLE movement ADD COLUMN unilateral BOOLEAN NOT NULL DEFAULT 0;
