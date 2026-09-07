-- 071_kickback_flat_ladder.sql — Better Fly Kickback [FT] (id 135),
-- [5, 2.5] -> flat [2.5] per athlete directive (2026-09-07): after today's
-- 65lb glute kickback set, future advancement should only ever step by
-- 2.5lb, matching the fix already applied to its sibling Better Fly Hip
-- Adduction [FT] in 070_leg_curl_hip_adduction_flat_ladder.sql. Same
-- never-flattened-tiered-ladder bug class, see project-ops memory
-- ironlogv2-tiered-increment-ladder-recurring-bug.md (4th occurrence).
UPDATE movement SET increment_ladder = '[2.5]', min_step = 2.5 WHERE id = 135 AND increment_ladder = '[5, 2.5]';
