-- 053_calf_raise_flat_ladder.sql — deploy the Calf Raise portion of commit
-- 4a29f4d's already-committed code (never applied live): Hybrid Board Calf
-- Raise [D2] and [D5] (separate rows, same apparatus), [5, 2.5] -> flat [5]
-- per athlete directive. (NOTE: that commit's Matrix Machine Bulgarian
-- Split Squat scheme change and its Reverse Nordic Curl ladder fix are
-- deliberately NOT in this file -- Reverse Nordic's live value already
-- matches the fix (no-op), and BSS conflicts with today's outside-review
-- approval of the current 8-12 DOUBLE_PROGRESSION state; flagged separately.)
UPDATE movement SET increment_ladder = '[5]', min_step = 5 WHERE id IN (126, 136) AND increment_ladder = '[5, 2.5]';
