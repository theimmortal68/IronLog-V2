-- 069_tib_raise_flat_ladder.sql — deploy commit f841388's already-committed
-- seed.py fix (never applied live): Hybrid Board Tib Raise [D2] and [D5]
-- (separate rows, same apparatus), [5, 2.5] -> flat [1.25] per athlete
-- directive (micro-plate granularity). Surfaced 2026-09-07 when the athlete
-- logged a 5lb jump on D5's Tib Bar Raise instead of the expected 1.25lb step
-- -- third recurrence of this bug class, see project-ops memory
-- ironlogv2-tiered-increment-ladder-recurring-bug.md.
UPDATE movement SET increment_ladder = '[1.25]', min_step = 1.25 WHERE id IN (137, 140) AND increment_ladder = '[5, 2.5]';
