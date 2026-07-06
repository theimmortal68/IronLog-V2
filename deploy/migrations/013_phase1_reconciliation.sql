-- 013_phase1_reconciliation.sql — Phase-1 seed reconciliation (Task 2)
-- Data-only: no schema change (unilateral, rest_seconds, rpe_cap, rep_low/
-- rep_high, scheme columns already exist). Every UPDATE is guarded with a
-- WHERE clause that also checks the column doesn't already equal the target
-- value, so this migration is idempotent and safe to re-run.

-- Movement.scheme: Belt Squat + RDL -> STRAIGHT (TOPSET_BACKOFF was wrong;
-- Bench Press is intentionally NOT included here, see task-2 report).
UPDATE movement SET scheme = 'STRAIGHT' WHERE name = 'Belt Squat [GHR + FT]' AND scheme != 'STRAIGHT';
UPDATE movement SET scheme = 'STRAIGHT' WHERE name = 'RDL [PB]' AND scheme != 'STRAIGHT';

-- Movement.unilateral: per-side movement flag on 8 movements.
UPDATE movement SET unilateral = 1 WHERE name = 'Meadows Row [OB + LM]' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'Bulgarian Split Squat [DB]' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'ATG Split Squat' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'Cross-Body Cable Rear Delt Fly [FT]' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'Cross-Body Cable Lateral Raise [FT]' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'Single-Arm DB Row [DB]' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'Poliquin Step-up' AND (unilateral IS NULL OR unilateral != 1);
UPDATE movement SET unilateral = 1 WHERE name = 'Staggered RDL [PB]' AND (unilateral IS NULL OR unilateral != 1);

-- TierExercise.rep_low/rep_high: literal rep targets (13 slot_ids).
UPDATE tierexercise SET rep_low = 6, rep_high = 8 WHERE slot_id = 'd1_t1' AND (rep_low IS NULL OR rep_low != 6 OR rep_high IS NULL OR rep_high != 8);
UPDATE tierexercise SET rep_low = 8, rep_high = 8 WHERE slot_id = 'd1_t2a' AND (rep_low IS NULL OR rep_low != 8 OR rep_high IS NULL OR rep_high != 8);
UPDATE tierexercise SET rep_low = 10, rep_high = 10 WHERE slot_id = 'd1_t2b' AND (rep_low IS NULL OR rep_low != 10 OR rep_high IS NULL OR rep_high != 10);
UPDATE tierexercise SET rep_low = 15, rep_high = 15 WHERE slot_id = 'd1_t2c' AND (rep_low IS NULL OR rep_low != 15 OR rep_high IS NULL OR rep_high != 15);
UPDATE tierexercise SET rep_low = 6, rep_high = 10 WHERE slot_id = 'd1_t3a' AND (rep_low IS NULL OR rep_low != 6 OR rep_high IS NULL OR rep_high != 10);
UPDATE tierexercise SET rep_low = 12, rep_high = 12 WHERE slot_id = 'd1_t3b' AND (rep_low IS NULL OR rep_low != 12 OR rep_high IS NULL OR rep_high != 12);
UPDATE tierexercise SET rep_low = 12, rep_high = 12 WHERE slot_id = 'd1_t3c' AND (rep_low IS NULL OR rep_low != 12 OR rep_high IS NULL OR rep_high != 12);
UPDATE tierexercise SET rep_low = 12, rep_high = 12 WHERE slot_id = 'd1_t4a' AND (rep_low IS NULL OR rep_low != 12 OR rep_high IS NULL OR rep_high != 12);
UPDATE tierexercise SET rep_low = 8, rep_high = 8 WHERE slot_id = 'd1_t4b' AND (rep_low IS NULL OR rep_low != 8 OR rep_high IS NULL OR rep_high != 8);
UPDATE tierexercise SET rep_low = 12, rep_high = 12 WHERE slot_id = 'd1_t4c' AND (rep_low IS NULL OR rep_low != 12 OR rep_high IS NULL OR rep_high != 12);
UPDATE tierexercise SET rep_low = 6, rep_high = 8 WHERE slot_id = 'd4_t1' AND (rep_low IS NULL OR rep_low != 6 OR rep_high IS NULL OR rep_high != 8);
UPDATE tierexercise SET rep_low = 8, rep_high = 12 WHERE slot_id = 'd6_g1b' AND (rep_low IS NULL OR rep_low != 8 OR rep_high IS NULL OR rep_high != 12);
UPDATE tierexercise SET rep_low = 10, rep_high = 15 WHERE slot_id = 'd5_t3d' AND (rep_low IS NULL OR rep_low != 10 OR rep_high IS NULL OR rep_high != 15);

-- TierExercise.rpe_cap: Reverse Hyper Recovery (D6 GS2, moved from GS3).
UPDATE tierexercise SET rpe_cap = 6.0 WHERE slot_id = 'd6_g2a' AND (rpe_cap IS NULL OR rpe_cap != 6.0);

-- Tier.rest_seconds: all 18 seeded tiers, keyed per (day_role, tier_label)
-- (rests are non-uniform per label after the YAML reconciliation).
UPDATE tier SET rest_seconds = 120 WHERE tier_label = 'T1' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D1 Upper Push') AND (rest_seconds IS NULL OR rest_seconds != 120);
UPDATE tier SET rest_seconds = 90 WHERE tier_label = 'T2 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D1 Upper Push') AND (rest_seconds IS NULL OR rest_seconds != 90);
UPDATE tier SET rest_seconds = 75 WHERE tier_label = 'T3 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D1 Upper Push') AND (rest_seconds IS NULL OR rest_seconds != 75);
UPDATE tier SET rest_seconds = 60 WHERE tier_label = 'T4 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D1 Upper Push') AND (rest_seconds IS NULL OR rest_seconds != 60);
UPDATE tier SET rest_seconds = 120 WHERE tier_label = 'T1' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D2 Lower A') AND (rest_seconds IS NULL OR rest_seconds != 120);
UPDATE tier SET rest_seconds = 150 WHERE tier_label = 'T1b' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D2 Lower A') AND (rest_seconds IS NULL OR rest_seconds != 150);
UPDATE tier SET rest_seconds = 90 WHERE tier_label = 'T2 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D2 Lower A') AND (rest_seconds IS NULL OR rest_seconds != 90);
UPDATE tier SET rest_seconds = 75 WHERE tier_label = 'T3' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D2 Lower A') AND (rest_seconds IS NULL OR rest_seconds != 75);
UPDATE tier SET rest_seconds = 180 WHERE tier_label = 'T1' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D4 Upper Pull') AND (rest_seconds IS NULL OR rest_seconds != 180);
UPDATE tier SET rest_seconds = 90 WHERE tier_label = 'T2 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D4 Upper Pull') AND (rest_seconds IS NULL OR rest_seconds != 90);
UPDATE tier SET rest_seconds = 75 WHERE tier_label = 'T3 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D4 Upper Pull') AND (rest_seconds IS NULL OR rest_seconds != 75);
UPDATE tier SET rest_seconds = 180 WHERE tier_label = 'T1' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D5 Lower B') AND (rest_seconds IS NULL OR rest_seconds != 180);
UPDATE tier SET rest_seconds = 150 WHERE tier_label = 'T1b' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D5 Lower B') AND (rest_seconds IS NULL OR rest_seconds != 150);
UPDATE tier SET rest_seconds = 90 WHERE tier_label = 'T2 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D5 Lower B') AND (rest_seconds IS NULL OR rest_seconds != 90);
UPDATE tier SET rest_seconds = 60 WHERE tier_label = 'T3 GS' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D5 Lower B') AND (rest_seconds IS NULL OR rest_seconds != 60);
UPDATE tier SET rest_seconds = 90 WHERE tier_label = 'GS1' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D6 Weak Points') AND (rest_seconds IS NULL OR rest_seconds != 90);
UPDATE tier SET rest_seconds = 90 WHERE tier_label = 'GS2' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D6 Weak Points') AND (rest_seconds IS NULL OR rest_seconds != 90);
UPDATE tier SET rest_seconds = 60 WHERE tier_label = 'GS3' AND program_day_id IN (SELECT id FROM programday WHERE day_role = 'D6 Weak Points') AND (rest_seconds IS NULL OR rest_seconds != 60);
