-- 067_periodization_schema.sql — long-range periodization schema.
-- Purely-additive schema: new planning/state tables plus nullable provenance
-- and mesocycle-link columns. Column types/nullability mirror SQLModel
-- create_all output for SQLite.

CREATE TABLE IF NOT EXISTS macrocycle (
    id INTEGER NOT NULL,
    goal VARCHAR NOT NULL,
    planned_start_date DATE,
    planned_end_date DATE,
    actual_start_date DATE,
    actual_end_date DATE,
    status VARCHAR(9) NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS mesocycletemplate (
    id INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    postures JSON,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_mesocycletemplate_name ON mesocycletemplate (name);

CREATE TABLE IF NOT EXISTS mesocycle (
    id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    macrocycle_id INTEGER,
    ordinal INTEGER,
    planned_start_date DATE NOT NULL,
    planned_end_date DATE NOT NULL,
    actual_start_date DATE,
    actual_end_date DATE,
    status VARCHAR(9) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(template_id) REFERENCES mesocycletemplate (id),
    FOREIGN KEY(macrocycle_id) REFERENCES macrocycle (id),
    UNIQUE (macrocycle_id, ordinal)
);

CREATE TABLE IF NOT EXISTS microcycle (
    id INTEGER NOT NULL,
    mesocycle_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    planned_start_date DATE NOT NULL,
    planned_end_date DATE NOT NULL,
    actual_start_date DATE,
    actual_completion_date DATE,
    expected_sessions INTEGER NOT NULL,
    completed_sessions INTEGER NOT NULL,
    lifecycle_status VARCHAR(11) NOT NULL,
    drift_status VARCHAR(13) NOT NULL,
    drift_days INTEGER NOT NULL,
    planned_posture VARCHAR NOT NULL,
    effective_posture VARCHAR,
    PRIMARY KEY (id),
    FOREIGN KEY(mesocycle_id) REFERENCES mesocycle (id),
    UNIQUE (mesocycle_id, ordinal)
);

CREATE TABLE IF NOT EXISTS bodycompstate (
    id INTEGER NOT NULL,
    state VARCHAR(11) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    notes VARCHAR,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS recoverystatus (
    id INTEGER NOT NULL,
    as_of_date DATE NOT NULL,
    status VARCHAR(7) NOT NULL,
    inputs_snapshot JSON,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_recoverystatus_as_of_date ON recoverystatus (as_of_date);

CREATE TABLE IF NOT EXISTS deloadstate (
    id INTEGER NOT NULL,
    microcycle_id INTEGER,
    active BOOLEAN NOT NULL,
    triggered_at DATE,
    trigger_reason VARCHAR,
    resolved_at DATE,
    PRIMARY KEY (id),
    FOREIGN KEY(microcycle_id) REFERENCES microcycle (id)
);

ALTER TABLE mesorotation ADD COLUMN mesocycle_id INTEGER REFERENCES mesocycle (id);
ALTER TABLE session ADD COLUMN prescription_snapshot JSON;
