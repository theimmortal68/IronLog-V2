-- 026_finisher_schema.sql — EMOM finisher schema foundation.
-- Additive only: new DayFinisher table plus nullable ladder/state columns.
CREATE TABLE IF NOT EXISTS dayfinisher (
    id INTEGER NOT NULL,
    program_day_id INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    params JSON,
    PRIMARY KEY (id),
    FOREIGN KEY(program_day_id) REFERENCES programday (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id)
);
ALTER TABLE movement ADD COLUMN rope_ladder JSON;
ALTER TABLE movementstate ADD COLUMN duration_ladder JSON;
ALTER TABLE movementstate ADD COLUMN current_duration_seconds INTEGER;
ALTER TABLE movementstate ADD COLUMN current_rope VARCHAR;
