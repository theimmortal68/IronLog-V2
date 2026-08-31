-- 046_slam_ball_lower_back_coaching_note.sql
--
-- D4 Upper B's slam_ball finisher is deliberately conservative (per the
-- athlete's finisher-placement reasoning, 2026-08-31: D5 Lower B follows
-- D4, so D4's finisher must not meaningfully fatigue the legs or spine).
-- Record the coaching caution directly on the movement so it's visible
-- wherever this finisher is displayed, not just in this migration's
-- commit history.
UPDATE movement
SET notes = 'D4 finisher, placed conservatively ahead of D5 Lower B. Keep technique clean -- do not let this drift into repeated loaded spinal flexion (athlete is managing a lower-back history).'
WHERE id = 113 AND name = 'slam_ball';
