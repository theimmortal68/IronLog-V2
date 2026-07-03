-- 016_progression_engine_schema.sql — progression-engine state + per-movement rule config.
-- Additive columns (ADD COLUMN is atomic in SQLite). The MovementState unique key moves
-- from (movement_id) to (movement_id, day_id): drop the old auto unique index, add day_id,
-- backfill it from each state's originating session, create the composite unique index.
-- Idempotent: ADD COLUMN guarded by the runner's per-file once-semantics; index ops use IF (NOT) EXISTS.
ALTER TABLE movementstate ADD COLUMN day_id VARCHAR;
ALTER TABLE movementstate ADD COLUMN consecutive_advance_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE movementstate ADD COLUMN active_rule VARCHAR;
ALTER TABLE movementstate ADD COLUMN current_body_position VARCHAR;
ALTER TABLE movementstate ADD COLUMN unassisted_max_rolling INTEGER;
ALTER TABLE movementstate ADD COLUMN stall_signal JSON;
ALTER TABLE movement ADD COLUMN progression_rule VARCHAR;
ALTER TABLE movement ADD COLUMN assist_ladder JSON;
ALTER TABLE movement ADD COLUMN position_ladder JSON;
ALTER TABLE movement ADD COLUMN rep_ladder JSON;
-- Backfill day_id: the day_role of the most-recent session that logged each movement.
-- (Existing rows are single-day per movement pre-composite, so this is unambiguous.)
UPDATE movementstate SET day_id = (
    SELECT s.day_role FROM setlog sl JOIN session s ON s.id = sl.session_id
    WHERE sl.movement_id = movementstate.movement_id
    ORDER BY s.id DESC LIMIT 1
) WHERE day_id IS NULL;
-- Old auto unique index on movement_id alone (confirmed real name via a fresh
-- create_all DB: `SELECT name FROM sqlite_master WHERE type='index' AND
-- tbl_name='movementstate'` -> 'ix_movementstate_movement_id', unique). It must
-- go or it still blocks two rows sharing movement_id once day_id differs.
DROP INDEX IF EXISTS ix_movementstate_movement_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_movementstate_movement_day ON movementstate (movement_id, day_id);
CREATE INDEX IF NOT EXISTS ix_movementstate_movement_id ON movementstate (movement_id);
CREATE INDEX IF NOT EXISTS ix_movementstate_day_id ON movementstate (day_id);
