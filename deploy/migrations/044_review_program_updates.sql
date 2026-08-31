-- 044_review_program_updates.sql — apply changes agreed with the athlete after an
-- outside (ChatGPT) review of the Post-HGC Phase 1 program, 2026-08-31.
--
-- NOT included here (deliberately deferred, flagged to the athlete):
--   * Pendlay/Bench alternating-pair tiers — the generation engine has no mechanism
--     for interleaving two non-giant tiers (assembler.py runs each tier as complete
--     straight sets); this needs a real engine change, scoped separately.
--   * D5 Russian-twist -> suitcase Dreadmill carry swap — the exercise is time-based
--     (20-30 sec/side) but TierExercise only carries rep_low/rep_high with no
--     duration field outside the DayFinisher/FINISHER_DURATION_THEN_ROPE path;
--     forcing seconds into the rep fields would corrupt double-progression math.

-- Program rename: Post-HGC Phase 1 (Pre-APEX Bridge) -> APEX Bridge (pre-VBS/Direct Flight).
UPDATE program SET name='APEX Bridge (Pre-VBS/Direct Flight)', phase='APEX_BRIDGE' WHERE id=1;

-- Item 1: rename Upper Push/Upper Pull day-role labels to Upper A/Upper B (the
-- program is a mixed-upper A/B split, not a true push/pull split).
-- MovementState.day_id is keyed to ProgramDay.day_role text (generation/context.py,
-- generation/assembler.py) -- must update both in lockstep or D1/D4 progression
-- state (e1RM, current_load, increment tier) orphans under the old label.
UPDATE programday SET day_role='D1 Upper A' WHERE id=1 AND day_role='D1 Upper Push';
UPDATE programday SET day_role='D4 Upper B' WHERE id=4 AND day_role='D4 Upper Pull';
UPDATE movementstate SET day_id='D1 Upper A' WHERE day_id='D1 Upper Push';
UPDATE movementstate SET day_id='D4 Upper B' WHERE day_id='D4 Upper Pull';

-- Item 4: D2 belt squat rest 150 -> 180 (unpaired heavy strength anchor).
UPDATE tier SET rest_seconds=180 WHERE id=5;

-- Item 6: D4 BTN OHP rest 120 -> 180 (unpaired heavy strength anchor).
UPDATE tier SET rest_seconds=180 WHERE id=9;

-- Item 8: replace D6 Swiss Bar CG Press with a dedicated standing strict OHP tier,
-- run straight (not paired/giant-setted) ahead of D6's existing giant sets, to give
-- the athlete's stated overhead-lockout weakness direct, undiluted practice.
INSERT INTO tier (program_day_id, tier_label, tier_order, tier_kind, rest_seconds, rounds, shoe)
VALUES (6, 'T1', 1, 'T1_STRAIGHT', 180, 1, 'Metcon 9');
UPDATE tier SET tier_order = tier_order + 1 WHERE id IN (16, 17, 18);  -- D6 GS1/GS2/GS3 shift down one
INSERT INTO tierexercise (tier_id, slot_id, movement_id, exercise_order, tier_role, rep_low, rep_high, rpe_cap, scheme)
VALUES ((SELECT id FROM tier WHERE program_day_id=6 AND tier_label='T1'), 'd6_t1_standing_ohp', 5, 1, 'anchor', 3, 5, 7.5, 'STRAIGHT');
DELETE FROM tierexercise WHERE id=68;  -- Swiss Bar CG Press [SB], superseded by the standing OHP tier above

-- Item 10: reorganize D5 giant sets so Bulgarian Split Squat and Reverse Nordic Curl
-- (both substantial knee-extensor/quad movements) aren't back-to-back in the same
-- giant set -- move Reverse Nordic into GS1 (pairs with the opposing hamstring-curl
-- action instead) and Better Fly Hip Adduction into GS2 to backfill.
UPDATE tierexercise SET tier_id=14, exercise_order=4 WHERE id=30;  -- Reverse Nordic Curl [GHR] -> GS1
UPDATE tierexercise SET tier_id=15, exercise_order=2 WHERE id=64;  -- Better Fly Hip Adduction [FT] -> GS2

-- Item 11: give previously-unscored core accessories an explicit progression scheme
-- instead of open-ended rep-range repetition.
UPDATE tierexercise SET scheme='DOUBLE_PROGRESSION' WHERE id=52;  -- D2 Ab Trainer Decline Sit-up
UPDATE tierexercise SET scheme='REP_LADDER' WHERE id=55;  -- D4 Ab Trainer Hanging Leg Raise
