CREATE TABLE IF NOT EXISTS goalsettings (
    id INTEGER NOT NULL,
    target_bodyweight FLOAT NOT NULL,
    target_bodyweight_tolerance FLOAT NOT NULL,
    target_body_fat_pct FLOAT,
    target_body_fat_pct_tolerance FLOAT,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
