-- 059_d2_decline_situp_to_t2.sql — athlete directive (2026-09-01): moves
-- Ab Trainer Decline Sit-up (te id 52) from D2's T3 GS (tier 8) to T2 GS
-- (tier 7), order 4, ahead of the D2 T3 restructure (ATG Split Squat + Tib
-- Raise + incoming Dreadmill Suitcase Carry). Fresh slot "d2_t2g" per the
-- never-reassign-slot_id convention (a tier move gets a fresh slot_id even
-- for an unchanged movement, same precedent as every prior tier-move this
-- program's history -- d2_t2a/b already vacated). Single UPDATE, naturally
-- idempotent.
UPDATE tierexercise SET tier_id = 7, slot_id = 'd2_t2g', exercise_order = 4
WHERE id = 52 AND tier_id = 8 AND slot_id = 'd2_t2f';
