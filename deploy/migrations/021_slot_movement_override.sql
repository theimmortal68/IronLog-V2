-- 021_slot_movement_override.sql — live-state per-slot movement swap (note-apply).
-- Additive CREATE TABLE; columns match SQLModel create_all output (parity test).
CREATE TABLE IF NOT EXISTS slotmovementoverride (
    id INTEGER NOT NULL,
    tier_exercise_id INTEGER NOT NULL,
    override_movement_id INTEGER NOT NULL,
    source_note_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    active BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tier_exercise_id) REFERENCES tierexercise (id),
    FOREIGN KEY(override_movement_id) REFERENCES movement (id),
    FOREIGN KEY(source_note_id) REFERENCES note (id)
);
CREATE INDEX IF NOT EXISTS ix_slotmovementoverride_tier_exercise_id ON slotmovementoverride (tier_exercise_id);
