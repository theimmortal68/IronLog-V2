-- 002_add_movementstate_consecutive_failed_progressions.sql
-- Adds movementstate.consecutive_failed_progressions (v0.4 PROGRESS gate counter).
-- NOT NULL requires DEFAULT in SQLite ALTER TABLE — DEFAULT 0 used here.
-- Aligned to create_all dflt_value: '0' (verified: PRAGMA table_info on a fresh
-- create_all DB with server_default=text("0") reports dflt_value='0'; ALTER TABLE
-- DEFAULT 0 also reports dflt_value='0' — both paths converge on '0').
ALTER TABLE movementstate ADD COLUMN consecutive_failed_progressions INTEGER NOT NULL DEFAULT 0;
