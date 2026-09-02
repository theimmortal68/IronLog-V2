-- 061_alternating_pair_d1_content.sql — content migration, depends on 060
-- landing first (references paired_tier_id). D1 Upper Push's Pendlay Row
-- (T1b, tier 2) and Bench Press (T1, tier 1) now point at each other, both
-- rest 90s, and Pendlay stays first in tier_order per athlete preference
-- ("pull first"). Two idempotent single-value UPDATEs (each guarded by
-- id + tier_label, unaffected by re-running).
UPDATE tier SET paired_tier_id = 2, rest_seconds = 90, tier_order = 2
WHERE id = 1 AND tier_label = 'T1';
UPDATE tier SET paired_tier_id = 1, rest_seconds = 90, tier_order = 1
WHERE id = 2 AND tier_label = 'T1b';
