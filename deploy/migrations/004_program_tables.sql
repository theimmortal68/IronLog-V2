-- 004_program_tables.sql — program definition layer (v0.6 evolving-seed prior)
-- Adds: program, programday, tier, tierexercise, mesorotation
-- Each CREATE TABLE is atomic and idempotent (IF NOT EXISTS).
-- Column types match SQLModel create_all output for SQLite (verified by parity test).
-- VARCHAR lengths for str-enum columns equal the longest enum member:
--   tier_kind  VARCHAR(11) — T1_STRAIGHT is 11 chars
--   knee_modality VARCHAR(6) — NORDIC is 6 chars (matches migration 001)

CREATE TABLE IF NOT EXISTS program (
    id INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    phase VARCHAR NOT NULL,
    duration_weeks INTEGER NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS programday (
    id INTEGER NOT NULL,
    program_id INTEGER NOT NULL,
    day_index INTEGER NOT NULL,
    day_role VARCHAR NOT NULL,
    is_rest BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(program_id) REFERENCES program (id)
);

CREATE TABLE IF NOT EXISTS tier (
    id INTEGER NOT NULL,
    program_day_id INTEGER NOT NULL,
    tier_label VARCHAR NOT NULL,
    tier_order INTEGER NOT NULL,
    tier_kind VARCHAR(11) NOT NULL,
    rest_seconds INTEGER,
    rounds INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(program_day_id) REFERENCES programday (id)
);

CREATE TABLE IF NOT EXISTS tierexercise (
    id INTEGER NOT NULL,
    tier_id INTEGER NOT NULL,
    slot_id VARCHAR NOT NULL,
    movement_id INTEGER NOT NULL,
    exercise_order INTEGER NOT NULL,
    tier_role VARCHAR NOT NULL,
    pattern VARCHAR,
    knee_modality VARCHAR(6),
    rep_low INTEGER,
    rep_high INTEGER,
    rpe_cap FLOAT,
    scheme VARCHAR,
    PRIMARY KEY (id),
    FOREIGN KEY(tier_id) REFERENCES tier (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id)
);

CREATE TABLE IF NOT EXISTS mesorotation (
    id INTEGER NOT NULL,
    tier_exercise_id INTEGER NOT NULL,
    meso_number INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tier_exercise_id) REFERENCES tierexercise (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id)
);
