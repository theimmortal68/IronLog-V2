CREATE TABLE IF NOT EXISTS misseddayrecord (
    id INTEGER NOT NULL,
    program_day_id INTEGER NOT NULL,
    week_start_date DATE NOT NULL,
    detected_at DATETIME NOT NULL,
    status VARCHAR NOT NULL,
    resolved_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(program_day_id) REFERENCES programday (id)
);
CREATE INDEX IF NOT EXISTS ix_misseddayrecord_program_day_id ON misseddayrecord (program_day_id);
