-- 018_shoe.sql — display-only footwear cue for the session graph (shoe-swap cue).
-- Additive columns (ADD COLUMN is atomic in SQLite); both are purely-additive
-- schema (VARCHAR, nullable, no backfill) -> allowed multi-statement per the
-- migrations README additive carve-out (2026-07-03).
ALTER TABLE tier ADD COLUMN shoe VARCHAR;
ALTER TABLE exercisegroup ADD COLUMN shoe VARCHAR;
