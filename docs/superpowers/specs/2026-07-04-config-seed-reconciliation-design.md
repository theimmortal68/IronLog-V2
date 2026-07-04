# Config-Seed Reconciliation (Engine Go-Live) — Design

**Date:** 2026-07-04
**Repo:** server `~/projects/IronLog-V2` (data + seed reconciliation; no client work)
**Status:** Design draft — PRESTAGE (D1/D2/D4/D5 in hand; **D6 blocked on Sat session**). Review before build.

## Goal

Make the progression engine actually adapt with real loads for Week 1: replace the **stale interim program structure** (D5 scramble, Sissy Squat, missing group_keys, HT mis-anchored) with the **authoritative D1–D6 program**, seed each movement's **progression config** (rule + ladders + initial states) so the merged engine is no longer dormant, and **reset all test data** so Week 1 starts from a clean, calibrated baseline. One deterministic re-seed pass.

## Approach: full re-seed (not incremental patch)

The live program is the messy interim structure; the DB is pre-launch/disposable. A **full re-seed** from the base-program YAML is cleaner than patching — it guarantees the correct structure (fixing the D5 scramble in one shot) and applies all config deterministically. Update the `_seed_d1..d6` builders in `ironlog/generation/program_seed.py` to the authoritative YAML, then run: wipe program + test data → `seed_phase1_program` → set `MovementState` loads/config → verify.

## Scope

| IN | OUT |
|---|---|
| Correct D1–D6 structure (tiers, `group_key`s per tier, anchors incl. **T1b HT as anchor**, meso rotations) | Warmups/activation/finishers/Z2 (v0.7, still not engine-managed) |
| Per-movement **progression_rule** + `assist_ladder`/`position_ladder`/`rep_ladder` (from the YAML) | Live-Gemini deviation (separate) |
| `MovementState` initial states: `current_load`, `assist_level`, `current_body_position`, `current_increment_tier`, `current_rep_target`; **HT `ht_plates` + `ht_band_config`** (band-composite is live) | The goal-aware deviation layer |
| Rep schemes: **T1 all `rep_low=6/rep_high=8`**; per-movement ranges from the YAML | Pull-up cross-day structural transitions (D4 milestone stays manual) |
| **Test-data reset** (see below) | |
| Go-live verification | |

## Per-day config (source = the base-program D1–D6 YAML)

Transcribe each day's tiers/movements/rules into `_seed_dN`, keyed to the locked Wk-1 baselines:
- **D1:** Bench 165 / Pendlay 170 / Incline-DB 55 / Face-Up-Knee 25° / Pull-up(rolling-max) / Lat-Raise 12.5 / Lat-Prayer 60 / Cable-Row 100 / Ab-Wheel(rep-ladder) / Rear-Delt 10. Shoes Metcon 9.
- **D2:** Belt-Squat 260(rep-ladder cap) / **HT 180 plates + [Orange]** (band-composite; rule_driven) / Nordic 20°(incline-reduction) / Scout-RH 180(rep-ladder) / ATG-Split-Squat 25 / Cable-Tib 25.
- **D4:** Pull-up(rolling-max, 2-phase) / Meadows 35 / SA-DB-Row 40 (T2/T3 swap applied) / Face-Up-Knee 10°(ladder starts 10°) / DB-Rear-Delt 10 / Andreoni-Pullover 70 / Dragon-Flag tuck(body-position). Shoes Metcon 9.
- **D5:** RDL 255 (meso: conventional→staggered) / **HT 205 plates + [Orange]** / Bulgarian 30 / Scout-RH-bilateral 180 / **Scout-RH-single-leg (Meso 2) = separate movement id, 70/side banked** / Nordic-light 25° / Poliquin 20 / Reverse-Nordic 20-assist(assistance-reduction) / Cable-Tib **30** (independent from D2's 25) / Calf 245. **Shoe swap: Metcon 9 → Adipower II at T3.**
- **D6:** *placeholder — fill from Saturday's session* (Pull-up weekly-max / Dips(BW rep-ladder) / **HT 130 plates + [Orange] + green-mini** (D5×0.80 derivative) / Kleva-T-Bar / DB-Seal-Row / Lateral-Raise / Face-Pull / V-Bar-Pushdown(single-session) / Reverse-Hyper-Recovery 90(fixed)).

**HT band-config seeding:** each HT gets `ht_plates` + `ht_band_config = [orange_band_id]` (D2/D5) or `[orange, green-mini]` (D6). Independent per-`(movement,day)` tracks (D2/D5 HT separate).

## Test-data reset

A logged test session already wrote state. The reset (server-side, disposable pre-launch) must:
1. Wipe transactional data: `SetLog`, `ExerciseSurvey`, `Note`, `GenerationLog`, `Session` (all rows).
2. Reset `MovementState` **not** to zero but to the **seeded calibrated baseline** — clear `e1rm`/`e1rm_updated_at`, `consecutive_failed_progressions`/`consecutive_advance_count`/`consecutive_ceiling_sessions`, `stall_signal`, `active_rule`, `unassisted_max_rolling`; delete `E1rmHistory`. Set `current_load`/`assist_level`/etc. to the seeded values.
3. Reset `EngineState` (phase CUT, bw current) as needed.
4. `BandPair` inventory already correct (band-composite chunk) — leave.

## Go-live verification

Per day: `generate_session(day_role)` → assert **clean tier order** (T1, T1b, T2, T3 — no interleave/Sissy), correct loads (the seeded baselines), HT shows plates + config + peak, the shoe cue reads right on D5, no needs-calibration on the calibrated movements. Full pytest suite green. Then the app is Week-1-ready.

## Build notes

- Update `program_seed._seed_d1..d6` + a `reconcile_and_reset(db)` routine; a runnable script (like `scripts/reconcile_phase1.py`).
- Deterministic + idempotent where possible; the reset is a wipe (disposable pre-launch) — **once real Week-1 logging starts, this is NOT re-runnable** (the backup/pull-before-push discipline resumes).
- NO `from __future__ import annotations`; no schema change (all fields exist from prior chunks).
- **Blocked on D6 seed data** (Sat) to fill `_seed_d6` + D6 `MovementState`; D1/D2/D4/D5 buildable now.
