-- 014_scheme_consistency.sql — Scheme consistency follow-up (Task 2 review fix)
-- Data-only: no schema change. Every UPDATE is guarded with a WHERE clause
-- that also checks the column doesn't already equal the target value, so
-- this migration is idempotent and safe to re-run.

-- Movement.scheme: Bench Press [PB] -> STRAIGHT. 013 deliberately left Bench
-- out (fixed live-only at the time); this closes the gap in the seed source
-- so a from-scratch reseed doesn't regress Bench to a 2-set top+backoff
-- (the 148.5-class bug). Live Bench is already STRAIGHT, so this is a no-op
-- there but correct for a from-scratch reapply. Back Squat / Front Squat /
-- OHP stay TOPSET_BACKOFF (out-of-Phase-1 alternates, dormant, not in scope).
UPDATE movement SET scheme = 'STRAIGHT' WHERE name = 'Bench Press [PB]' AND scheme != 'STRAIGHT';

-- TierExercise.scheme: sync the string field for the three T1 slots whose
-- Movement.scheme was flipped to STRAIGHT (d1_t1 Bench, d2_t1 Belt Squat,
-- d5_t1 RDL). The deterministic assembler reads Movement.scheme (already
-- correct) and ignores TierExercise.scheme, so there is no session-plan
-- corruption from the stale value — but generation/context.py's
-- build_context_payload() reads te.scheme into slot_rep_schemes, which
-- flows into the injected LLM prompt, so the model was seeing a stale
-- TOPSET_BACKOFF label for these slots.
UPDATE tierexercise SET scheme = 'STRAIGHT' WHERE slot_id = 'd1_t1' AND scheme != 'STRAIGHT';
UPDATE tierexercise SET scheme = 'STRAIGHT' WHERE slot_id = 'd2_t1' AND scheme != 'STRAIGHT';
UPDATE tierexercise SET scheme = 'STRAIGHT' WHERE slot_id = 'd5_t1' AND scheme != 'STRAIGHT';
