CREATE TABLE IF NOT EXISTS dailyreadiness (
    id INTEGER NOT NULL,
    date DATE NOT NULL,
    bodyweight FLOAT,
    bodyweight_source VARCHAR NOT NULL,
    resting_hr FLOAT,
    resting_hr_source VARCHAR NOT NULL,
    sleep_ok BOOLEAN,
    subjective_ok BOOLEAN,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (date)
);
