-- 065_d4_t2_t3_giant_set_reorder.sql — athlete directive (2026-09-03): swaps
-- Cable Pullover and Lying Tricep Extension between D4's T2 GS and T3 GS --
-- Stryker Pad CSR Barbell + Cable Pullover together in T2 was too much back
-- work back-to-back within the same giant set. Lying Tricep Extension moves
-- IN to T2 GS (fresh slot "d4_t2g", never-reassign-slot_id -- d4_t3e stays
-- vacated); Cable Pullover moves IN to T3 GS (fresh slot "d4_t3g" --
-- d4_t2f stays vacated). Also fixes exercise_order for the member that
-- stays in place but shifts position: Ab Trainer Hanging Leg Raise (T3 GS,
-- 3->2). Three single-row UPDATEs, each independently idempotent by its
-- own WHERE clause (id + pre-move tier_id/slot_id/exercise_order) -- a
-- partial-run re-execution is a no-op for whichever rows already moved.

UPDATE tierexercise SET tier_id = 10, slot_id = 'd4_t2g', exercise_order = 3
WHERE id = 57 AND tier_id = 11 AND slot_id = 'd4_t3e';

UPDATE tierexercise SET tier_id = 11, slot_id = 'd4_t3g', exercise_order = 3
WHERE id = 56 AND tier_id = 10 AND slot_id = 'd4_t2f';

UPDATE tierexercise SET exercise_order = 2
WHERE id = 55 AND exercise_order = 3;
