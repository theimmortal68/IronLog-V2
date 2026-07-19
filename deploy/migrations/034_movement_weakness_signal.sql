CREATE TABLE IF NOT EXISTS movementweaknesssignal (
    id INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    computed_at DATETIME NOT NULL,
    stalled BOOLEAN NOT NULL,
    growth_rate FLOAT,
    lagging BOOLEAN NOT NULL,
    is_weak BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id),
    FOREIGN KEY(session_id) REFERENCES session (id)
);
CREATE INDEX IF NOT EXISTS ix_movementweaknesssignal_movement_id ON movementweaknesssignal (movement_id);
