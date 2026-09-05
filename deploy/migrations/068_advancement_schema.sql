-- 068_advancement_schema.sql - advancement schema foundation.
--
-- SQLite cannot add a NOT NULL column without a database default to a table
-- that may already contain rows, and it cannot later ALTER a column to drop a
-- temporary default. Macrocycle.planning_state and Session.plan_status must end
-- as NOT NULL with no DB default, so those two tables are rebuilt inside one
-- explicit transaction. The live backfills are intentionally fixed to the
-- production facts for this cutover: Macrocycle id=1 -> ACTIVE, Mesocycle id=1
-- -> Program id=1, and all pre-migration Session rows -> LEGACY.

PRAGMA foreign_keys = OFF;
PRAGMA legacy_alter_table = ON;

BEGIN;

CREATE TEMP TABLE advancement_schema_068_guard (
    ok INTEGER NOT NULL CHECK (ok = 1)
);

-- Stop rather than guessing if the live singleton rows are not the expected ids.
INSERT INTO advancement_schema_068_guard
SELECT CASE WHEN COUNT(*) = 0 OR (COUNT(*) = 1 AND MAX(id) = 1) THEN 1 ELSE NULL END
FROM program;
INSERT INTO advancement_schema_068_guard
SELECT CASE WHEN COUNT(*) = 0 OR (COUNT(*) = 1 AND MAX(id) = 1) THEN 1 ELSE NULL END
FROM macrocycle;
INSERT INTO advancement_schema_068_guard
SELECT CASE WHEN COUNT(*) = 0 OR (COUNT(*) = 1 AND MAX(id) = 1) THEN 1 ELSE NULL END
FROM mesocycle;
INSERT INTO advancement_schema_068_guard
SELECT CASE WHEN COUNT(*) = 0 OR (COUNT(*) = 1 AND MAX(id) = 1) THEN 1 ELSE NULL END
FROM microcycle;

ALTER TABLE macrocycle RENAME TO macrocycle_068_old;
CREATE TABLE macrocycle (
    id INTEGER NOT NULL,
    goal VARCHAR NOT NULL,
    planned_start_date DATE,
    planned_end_date DATE,
    actual_start_date DATE,
    actual_end_date DATE,
    status VARCHAR(9) NOT NULL,
    planning_state VARCHAR(23) NOT NULL,
    PRIMARY KEY (id)
);
INSERT INTO macrocycle (
    id,
    goal,
    planned_start_date,
    planned_end_date,
    actual_start_date,
    actual_end_date,
    status,
    planning_state
)
SELECT
    id,
    goal,
    planned_start_date,
    planned_end_date,
    actual_start_date,
    actual_end_date,
    status,
    CASE WHEN id = 1 THEN 'ACTIVE' ELSE NULL END
FROM macrocycle_068_old;
DROP TABLE macrocycle_068_old;

ALTER TABLE mesocycle ADD COLUMN program_id INTEGER REFERENCES program (id);
ALTER TABLE mesocycle ADD COLUMN program_prescription_hash VARCHAR;
UPDATE mesocycle
SET program_id = 1
WHERE id = 1 AND program_id IS NULL;

ALTER TABLE microcycle ADD COLUMN slot_topology_hash VARCHAR;
UPDATE microcycle
SET lifecycle_status = 'INCOMPLETE'
WHERE lifecycle_status = 'ABORTED';

CREATE TABLE IF NOT EXISTS advancementlog (
    id INTEGER NOT NULL,
    reconcile_run_id VARCHAR,
    entity_type VARCHAR NOT NULL,
    entity_id INTEGER NOT NULL,
    reason VARCHAR NOT NULL,
    details_json JSON,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);

ALTER TABLE session RENAME TO session_068_old;
CREATE TABLE session (
    id INTEGER NOT NULL,
    date DATE NOT NULL,
    day_role VARCHAR NOT NULL,
    phase VARCHAR NOT NULL,
    prescription_snapshot JSON,
    status VARCHAR(11) NOT NULL,
    generated_at DATETIME NOT NULL,
    approved_at DATETIME,
    analyzed_at DATETIME,
    signature JSON,
    rationale VARCHAR,
    notes VARCHAR,
    microcycle_id INTEGER,
    plan_status VARCHAR(9) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(microcycle_id) REFERENCES microcycle (id) ON DELETE RESTRICT
);
INSERT INTO session (
    id,
    date,
    day_role,
    phase,
    prescription_snapshot,
    status,
    generated_at,
    approved_at,
    analyzed_at,
    signature,
    rationale,
    notes,
    microcycle_id,
    plan_status
)
SELECT
    id,
    date,
    day_role,
    phase,
    prescription_snapshot,
    status,
    generated_at,
    approved_at,
    analyzed_at,
    signature,
    rationale,
    notes,
    NULL,
    'LEGACY'
FROM session_068_old;
DROP TABLE session_068_old;
CREATE INDEX IF NOT EXISTS ix_session_microcycle_id ON session (microcycle_id);

CREATE TABLE IF NOT EXISTS microcycleslot (
    id INTEGER NOT NULL,
    microcycle_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    day_code VARCHAR NOT NULL,
    day_label VARCHAR NOT NULL,
    planned_date DATE NOT NULL,
    slot_type VARCHAR(8) NOT NULL,
    resolution VARCHAR(14) NOT NULL,
    resolution_source VARCHAR(17),
    session_id INTEGER,
    resolved_at DATETIME,
    PRIMARY KEY (id),
    UNIQUE (microcycle_id, ordinal),
    UNIQUE (microcycle_id, day_code),
    UNIQUE (session_id),
    FOREIGN KEY(microcycle_id) REFERENCES microcycle (id),
    FOREIGN KEY(session_id) REFERENCES session (id) ON DELETE RESTRICT
);

DROP TABLE advancement_schema_068_guard;

COMMIT;

PRAGMA legacy_alter_table = OFF;
PRAGMA foreign_keys = OFF;
