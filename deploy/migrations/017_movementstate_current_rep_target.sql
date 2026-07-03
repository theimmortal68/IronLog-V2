-- 017_movementstate_current_rep_target.sql — rep-ladder rule state: per-movement rep target.
-- Additive column (ADD COLUMN is atomic in SQLite).
ALTER TABLE movementstate ADD COLUMN current_rep_target INTEGER;
