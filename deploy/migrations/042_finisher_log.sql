-- 042_finisher_log.sql — finisher performance logging.
-- Additive CREATE TABLE; columns match SQLModel create_all output.
CREATE TABLE IF NOT EXISTS finisherlog (
    id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    actual_weight_lb REAL,
    actual_resistance_level INTEGER,
    notes TEXT,
    performed_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(session_id) REFERENCES session (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id)
);
CREATE INDEX IF NOT EXISTS ix_finisherlog_session_id ON finisherlog (session_id);
CREATE INDEX IF NOT EXISTS ix_finisherlog_movement_id ON finisherlog (movement_id);
