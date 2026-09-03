-- 066_d6_gs2_reorder.sql — athlete directive (2026-09-03): reorders D6 GS2
-- so the compound pull (Stryker Pad CSR Cables) comes before the isolation
-- biceps curl (Better Fly Cable Bicep Curl) -- the row shouldn't be
-- pre-fatigued by curling first. No tier move, slot_ids unchanged, just
-- exercise_order. Two single-row UPDATEs, each independently idempotent by
-- its own WHERE clause (id + pre-move exercise_order) -- a partial-run
-- re-execution is a no-op for whichever row already moved.

UPDATE tierexercise SET exercise_order = 1
WHERE id = 70 AND exercise_order = 2;

UPDATE tierexercise SET exercise_order = 2
WHERE id = 76 AND exercise_order = 1;
