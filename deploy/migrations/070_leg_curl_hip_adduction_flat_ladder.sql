-- 070_leg_curl_hip_adduction_flat_ladder.sql — deploy the remainder of
-- commit f841388's already-committed seed.py fix (never applied live):
-- Lying Leg Curl [GHR + Ares] and Better Fly Hip Adduction [FT],
-- [5, 2.5] -> flat [2.5] per athlete directive. Companion to
-- 069_tib_raise_flat_ladder.sql (same commit, same never-deployed gap).
-- NOTE: Lying Leg Curl [GHR] (id 28, no Ares) is a separate, unrelated
-- movement that intentionally stays [5, 2.5] -- not touched here.
UPDATE movement SET increment_ladder = '[2.5]', min_step = 2.5 WHERE id IN (138, 151) AND increment_ladder = '[5, 2.5]';
