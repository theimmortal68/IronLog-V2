CREATE TABLE IF NOT EXISTS cardiolog (
    id INTEGER NOT NULL,
    date DATE NOT NULL,
    duration_minutes INTEGER NOT NULL,
    avg_hr INTEGER,
    modality VARCHAR NOT NULL,
    incline_pct FLOAT,
    backward_walk_done BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
