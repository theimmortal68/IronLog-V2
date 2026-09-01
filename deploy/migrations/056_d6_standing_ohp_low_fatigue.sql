-- 056_d6_standing_ohp_low_fatigue.sql — outside-review reconciliation
-- (athlete directive, 2026-09-01): D6 Standing OHP was still carrying its
-- original migration-044 prescription (3-5 reps @ RPE 7.5), which the
-- review flagged as "the earlier version" -- D6's role is low-fatigue
-- standing-OHP specificity/lockout practice/scapular stability, not a
-- strength grind (D4 already owns heavy overhead work, D1 owns volume).
-- Tightens to 3 reps @ RPE 6-7 (rpe_cap=7, the ceiling of that zone --
-- rpe_cap semantics are a "don't push past" cap, not a target-zone
-- midpoint), 3 sets is already the assembler's default for a STRAIGHT/
-- T1_STRAIGHT tier (assembler.py's "Everything else: 3 WORKING sets"
-- convention), so no rounds/set-count field changes are needed. rest_seconds
-- was already 180s (unchanged). Movement 5 is wired only on D6 (grep-
-- confirmed), so the coaching-note addition is safe -- same precedent as
-- migration 046's slam_ball note.
UPDATE tierexercise SET rep_low = 3, rep_high = 3, rpe_cap = 7 WHERE id = 77;
UPDATE movement SET notes = 'Low-fatigue specificity exposure, not a strength grind -- do not chase 5 reps at RPE 7.5-8. Target RPE 6-7. Deliberate 1-2 second lockout on every rep.' WHERE id = 5;
