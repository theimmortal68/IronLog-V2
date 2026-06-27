CREATE TABLE IF NOT EXISTS e1rmhistory (
    id INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    e1rm FLOAT NOT NULL,
    objective VARCHAR(8) NOT NULL,
    phase VARCHAR(11) NOT NULL,
    anchor_load FLOAT NOT NULL,
    anchor_reps INTEGER NOT NULL,
    anchor_rpe FLOAT NOT NULL,
    computed_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id),
    FOREIGN KEY(session_id) REFERENCES session (id)
);
CREATE INDEX IF NOT EXISTS ix_e1rmhistory_movement_id ON e1rmhistory (movement_id);
CREATE INDEX IF NOT EXISTS ix_e1rmhistory_session_id ON e1rmhistory (session_id);
