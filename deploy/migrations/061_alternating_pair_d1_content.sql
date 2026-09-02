-- 061_alternating_pair_d1_content.sql — content migration, depends on 060
-- landing first (references paired_tier_id). D1 Upper Push's Pendlay Row
-- (T1b) and Bench Press (T1) now point at each other, both rest 90s, and
-- Pendlay stays first in tier_order per athlete preference ("pull first").
--
-- Fixed 2026-09-02 (Fable review finding, caught on scratch-copy dry run
-- before this ever touched live): the original version of this file
-- assumed a fresh-reseed's tier ids (T1=1, T1b=2, matching program_seed.py's
-- _seed_d1 creation order), but T1b was created out-of-order at some point
-- in this live DB's real history and actually has id=21, not 2 -- T1's
-- UPDATE matched fine, but T1b's silently no-op'd (id=2 there is T2 GS,
-- tier_label != 'T1b', so the WHERE simply matched zero rows with no
-- error). Re-verified ids directly against the live DB rather than
-- program_seed.py's assumed ordering, and added a program_day_id guard as
-- defense-in-depth against exactly this class of drift.
UPDATE tier SET paired_tier_id = 21, rest_seconds = 90, tier_order = 2
WHERE id = 1 AND tier_label = 'T1' AND program_day_id = 1;
UPDATE tier SET paired_tier_id = 1, rest_seconds = 90, tier_order = 1
WHERE id = 21 AND tier_label = 'T1b' AND program_day_id = 1;
