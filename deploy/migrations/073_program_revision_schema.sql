-- 073_program_revision_schema.sql — ProgramRevision schema + child tables
-- for the immutable publish lifecycle (ADR 0001 Phase 1 §1.2).
--
-- 7 new tables: programrevision, programrevisionday, programrevisiontier,
-- programrevisionexercise, programrevisionmesorotation,
-- programrevisionparityrotation, programrevisionequipmentrequirement.
-- Additive only — no existing tables touched.
--
-- DDL matches SQLModel.metadata.create_all() output exactly (verified via
-- test_chain_matches_create_all).

CREATE TABLE IF NOT EXISTS programrevision (
    id              INTEGER NOT NULL PRIMARY KEY,
    program_id      INTEGER NOT NULL REFERENCES program(id),
    revision_number INTEGER NOT NULL,
    revision_origin VARCHAR(21) NOT NULL,
    prescription_hash VARCHAR NOT NULL,
    topology_hash   VARCHAR NOT NULL,
    created_at      DATETIME NOT NULL,
    UNIQUE (program_id, revision_number)
);
CREATE INDEX IF NOT EXISTS ix_programrevision_program_id ON programrevision(program_id);

CREATE TABLE IF NOT EXISTS programrevisionday (
    id                    INTEGER NOT NULL PRIMARY KEY,
    program_revision_id   INTEGER NOT NULL REFERENCES programrevision(id),
    day_index             INTEGER NOT NULL,
    day_role              VARCHAR NOT NULL,
    is_rest               BOOLEAN NOT NULL,
    warmup_config         JSON
);
CREATE INDEX IF NOT EXISTS ix_programrevisionday_program_revision_id ON programrevisionday(program_revision_id);

CREATE TABLE IF NOT EXISTS programrevisiontier (
    id                                INTEGER NOT NULL PRIMARY KEY,
    program_revision_day_id           INTEGER NOT NULL REFERENCES programrevisionday(id),
    paired_program_revision_tier_id   INTEGER REFERENCES programrevisiontier(id),
    tier_label                        VARCHAR NOT NULL,
    tier_order                        INTEGER NOT NULL,
    tier_kind                         VARCHAR(11) NOT NULL,
    rest_seconds                      INTEGER,
    rounds                            INTEGER NOT NULL,
    shoe                              VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_programrevisiontier_program_revision_day_id ON programrevisiontier(program_revision_day_id);

CREATE TABLE IF NOT EXISTS programrevisionexercise (
    id                         INTEGER NOT NULL PRIMARY KEY,
    program_revision_tier_id   INTEGER NOT NULL REFERENCES programrevisiontier(id),
    slot_id                    VARCHAR NOT NULL,
    movement_id                INTEGER NOT NULL REFERENCES movement(id),
    exercise_order             INTEGER NOT NULL,
    tier_role                  VARCHAR NOT NULL,
    pattern                    VARCHAR,
    knee_modality              VARCHAR(6),
    rep_low                    INTEGER,
    rep_high                   INTEGER,
    duration_low_seconds       INTEGER,
    duration_high_seconds      INTEGER,
    rpe_cap                    FLOAT,
    scheme                     VARCHAR,
    unified_ht_group           VARCHAR,
    derived_from_unified_group VARCHAR,
    derive_ratio               FLOAT,
    slot_role                  VARCHAR(13) NOT NULL,
    slot_constraints           JSON
);
CREATE INDEX IF NOT EXISTS ix_programrevisionexercise_program_revision_tier_id ON programrevisionexercise(program_revision_tier_id);

CREATE TABLE IF NOT EXISTS programrevisionmesorotation (
    id                                INTEGER NOT NULL PRIMARY KEY,
    program_revision_exercise_id      INTEGER NOT NULL REFERENCES programrevisionexercise(id),
    meso_number                       INTEGER NOT NULL,
    movement_id                       INTEGER NOT NULL REFERENCES movement(id),
    rep_low                           INTEGER,
    rep_high                          INTEGER
);
CREATE INDEX IF NOT EXISTS ix_programrevisionmesorotation_program_revision_exercise_id ON programrevisionmesorotation(program_revision_exercise_id);

CREATE TABLE IF NOT EXISTS programrevisionparityrotation (
    id                                INTEGER NOT NULL PRIMARY KEY,
    program_revision_exercise_id      INTEGER NOT NULL REFERENCES programrevisionexercise(id),
    week_parity                       VARCHAR NOT NULL,
    movement_id                       INTEGER NOT NULL REFERENCES movement(id),
    rep_low                           INTEGER,
    rep_high                          INTEGER,
    UNIQUE (program_revision_exercise_id, week_parity)
);
CREATE INDEX IF NOT EXISTS ix_programrevisionparityrotation_program_revision_exercise_id ON programrevisionparityrotation(program_revision_exercise_id);

CREATE TABLE IF NOT EXISTS programrevisionequipmentrequirement (
    id                                INTEGER NOT NULL PRIMARY KEY,
    program_revision_exercise_id      INTEGER NOT NULL REFERENCES programrevisionexercise(id),
    requirement_expr                  JSON
);
CREATE INDEX IF NOT EXISTS ix_programrevisionequipmentrequirement_program_revision_exercise_id ON programrevisionequipmentrequirement(program_revision_exercise_id);
