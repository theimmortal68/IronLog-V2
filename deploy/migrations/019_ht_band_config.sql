-- 019_ht_band_config.sql — HT band-composite: stackable band configuration (JSON list of band ids).
-- Purely-additive schema (ADD COLUMN, nullable JSON) -> allowed multi-statement per the README carve-out.
ALTER TABLE movementstate ADD COLUMN ht_band_config JSON;
ALTER TABLE plannedset ADD COLUMN band_config JSON;
