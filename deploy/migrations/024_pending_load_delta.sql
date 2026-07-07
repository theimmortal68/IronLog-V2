-- 024_pending_load_delta.sql — advance->load bridge (K2): earned load step marker.
-- Additive, nullable column. run_analysis stages the earned increment here (never
-- current_load — two-writer boundary intact); commit_session applies it to
-- current_load and clears it, so the bump lands exactly once.
-- ADD COLUMN is atomic in SQLite. Type matches the model's Optional[float]
-- (CreateTable -> "pending_load_delta FLOAT") so test_chain_matches_create_all stays green.
ALTER TABLE movementstate ADD COLUMN pending_load_delta FLOAT;
