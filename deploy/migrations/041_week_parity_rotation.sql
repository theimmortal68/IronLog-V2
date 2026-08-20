-- 041_week_parity_rotation.sql — automatic fixed-week parity slot rotation.
-- Additive CREATE TABLE; columns match SQLModel create_all output.
CREATE TABLE IF NOT EXISTS weekparityrotation (
    id INTEGER NOT NULL,
    tier_exercise_id INTEGER NOT NULL,
    week_parity VARCHAR NOT NULL,
    movement_id INTEGER NOT NULL,
    rep_low INTEGER,
    rep_high INTEGER,
    PRIMARY KEY (id),
    UNIQUE (tier_exercise_id, week_parity),
    FOREIGN KEY(tier_exercise_id) REFERENCES tierexercise (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id)
);
CREATE INDEX IF NOT EXISTS ix_weekparityrotation_tier_exercise_id ON weekparityrotation (tier_exercise_id);
