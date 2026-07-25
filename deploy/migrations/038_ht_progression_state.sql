CREATE TABLE IF NOT EXISTS htprogressionstate (
    id INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    unified_ht_group VARCHAR NOT NULL,
    ht_plates FLOAT NOT NULL,
    ht_band_config JSON NOT NULL,
    pending_ht_plates FLOAT,
    pending_ht_band_config JSON,
    calibration_status VARCHAR(11) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (movement_id, unified_ht_group),
    FOREIGN KEY(movement_id) REFERENCES movement (id)
);
ALTER TABLE tierexercise ADD COLUMN unified_ht_group VARCHAR;
