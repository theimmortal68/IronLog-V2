-- 064_d1_t2_t3_giant_set_reorder.sql — athlete directive (2026-09-03): swaps
-- Sagittal Lat Pulldown and Lateral Raise between D1's T2 GS and T3 GS --
-- Wide-Grip Pull-up + Sagittal Lat Pulldown together in T3 was too much lat
-- work back-to-back within the same giant set. Sagittal Lat Pulldown moves
-- IN to T2 GS (fresh slot "d1_t2h", never-reassign-slot_id -- d1_t3e stays
-- vacated); Lateral Raise moves IN to T3 GS (fresh slot "d1_t3f" --
-- d1_t2e stays vacated). Also fixes exercise_order for the two members that
-- stay in place but shift position: Matrix Machine Preacher Curl (T2 GS,
-- 2->3) and Ab Wheel (T3 GS, 3->2 -- this also resolves a pre-existing
-- duplicate-order-3 bug between Ab Wheel and Sagittal Lat Pulldown). Four
-- single-row UPDATEs, each independently idempotent by its own WHERE
-- clause (id + pre-move tier_id/slot_id/exercise_order) -- a partial-run
-- re-execution is a no-op for whichever rows already moved.

UPDATE tierexercise SET tier_id = 2, slot_id = 'd1_t2h', exercise_order = 2
WHERE id = 74 AND tier_id = 3 AND slot_id = 'd1_t3e';

UPDATE tierexercise SET tier_id = 3, slot_id = 'd1_t3f', exercise_order = 3
WHERE id = 47 AND tier_id = 2 AND slot_id = 'd1_t2e';

UPDATE tierexercise SET exercise_order = 3
WHERE id = 46 AND exercise_order = 2;

UPDATE tierexercise SET exercise_order = 2
WHERE id = 48 AND exercise_order = 3;
