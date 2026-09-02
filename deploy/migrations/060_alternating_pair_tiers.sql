-- 060_alternating_pair_tiers.sql — schema only, split from the content
-- migration (061) per this repo's migration-authoring rule (README.md):
-- data UPDATEs must stay single-statement/idempotent and separate from a
-- non-idempotent schema ALTER, so a partial failure can't leave the ALTER
-- committed-but-unrecorded while a retry hits a duplicate-column error.
--
-- Nullable self-FK on tier so either side of an alternating pair can find
-- its partner without relying on adjacent tier_order inference. Index
-- matches the SQLModel field and keeps partner lookups cheap.
ALTER TABLE tier ADD COLUMN paired_tier_id INTEGER REFERENCES tier(id);
CREATE INDEX IF NOT EXISTS ix_tier_paired_tier_id ON tier (paired_tier_id);
