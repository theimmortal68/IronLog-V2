-- 020_note_classification_meta.sql — note-confirm: parsed classification metadata
-- {proposed_change, confidence, rationale}. Purely-additive (ADD COLUMN nullable JSON)
-- -> allowed per the deploy/migrations/README.md carve-out.
ALTER TABLE note ADD COLUMN classification_meta JSON;
