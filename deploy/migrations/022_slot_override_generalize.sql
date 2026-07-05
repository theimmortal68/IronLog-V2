-- 022_slot_override_generalize.sql — generalize slotmovementoverride from a
-- pure movement-swap row into a general per-slot override (movement / load /
-- rep-target). Additive ADD COLUMNs; columns match SQLModel create_all output
-- (parity test). override_type has a server_default so PRAGMA table_info's
-- dflt_value matches create_all exactly (see sa_column_kwargs on the model).
ALTER TABLE slotmovementoverride ADD COLUMN override_type VARCHAR(8) DEFAULT 'MOVEMENT' NOT NULL;
ALTER TABLE slotmovementoverride ADD COLUMN load_delta FLOAT;
ALTER TABLE slotmovementoverride ADD COLUMN load_absolute FLOAT;
ALTER TABLE slotmovementoverride ADD COLUMN rep_low INTEGER;
ALTER TABLE slotmovementoverride ADD COLUMN rep_high INTEGER;
