-- 005_generation_log.sql — GenerationLog provenance table (Fork 7d, v0.6)
-- Full replayable audit trail: injected prompt, selections, clamps, repairs,
-- approval-mode, fallback-used, and commit timestamp (docs/06 §10).
-- Column types match SQLModel create_all output for SQLite (verified by parity test).
-- JSON columns are nullable (no NOT NULL): Column(JSON) without nullable=False
-- yields NULL-allowed in SQLAlchemy's DDL — model is the source of truth.
-- Two statements, both IF NOT EXISTS (idempotent per migrate.py authoring contract).

CREATE TABLE IF NOT EXISTS generationlog (
    id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    prompt_json JSON,
    selections_json JSON,
    clamps_json JSON,
    repairs_json JSON,
    approval_mode VARCHAR NOT NULL,
    fallback_used BOOLEAN NOT NULL,
    committed_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(session_id) REFERENCES session (id)
);
CREATE INDEX IF NOT EXISTS ix_generationlog_session_id ON generationlog (session_id);
