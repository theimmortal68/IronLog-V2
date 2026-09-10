# Spec 19: Sync the 19-exercise rep-range standardization across seed sources

## Objective
Athlete directive: every `DOUBLE_PROGRESSION`/`STRAIGHT`-scheme working-set exercise should target one of four rep buckets — 6-8, 8-12, 10-15, 15-20 — with movements using a special scheme (COMPOSITE, FIXED, REP_AT_CAP, ASSISTED, REP_RATIO, SINGLE_SESSION, or `scheme=None` protocol-style movements like core holds) explicitly exempted. The live production `TierExercise` rows for 19 mismatched slots have ALREADY been updated directly (data-only mutation, confirmed with the athlete, done to immediately unblock a live D2 Lower A generation). This spec syncs the three seed-source-of-truth files so a future fresh reseed doesn't regress them back to the old values — mirroring exactly the pattern already used for the D1 pull-up rep-range sync (spec 18, merged 2026-07-14).

## The exact mapping (already applied live — this spec only needs the same values in the seed sources)
| slot_id | day | movement | old (low, high) | new (low, high) |
|---|---|---|---|---|
| d1_t2a | D1 Upper Push | Pendlay Row - Narrow | (8, 8) | (8, 12) |
| d1_t2b | D1 Upper Push | Incline DB Press | (10, 10) | (8, 12) |
| d1_t3b | D1 Upper Push | Cross-Body Cable Lateral Raise | (12, 12) | (8, 12) |
| d1_t3c | D1 Upper Push | Lat Prayer | (12, 12) | (8, 12) |
| d1_t4a | D1 Upper Push | Seated Cable Row | (12, 12) | (8, 12) |
| d1_t4c | D1 Upper Push | Cross-Body Cable Rear Delt Fly | (12, 12) | (8, 12) |
| d2_t3a | D2 Lower A | ATG Split Squat | (8, 10) | (8, 12) |
| d2_t3b | D2 Lower A | Cable Tibialis Raise | (12, 15) | (10, 15) |
| d4_t2a | D4 Upper Pull | Meadows Row | (10, 10) | (8, 12) |
| d4_t2b | D4 Upper Pull | Single-Arm DB Row | (12, 12) | (8, 12) |
| d4_t3a | D4 Upper Pull | Rear Delt Fly | (12, 12) | (8, 12) |
| d4_t3b | D4 Upper Pull | Andreoni Cable Pullover | (12, 12) | (8, 12) |
| d5_t2a | D5 Lower B | Bulgarian Split Squat | (8, 10) | (8, 12) |
| d5_t2b | D5 Lower B | Reverse Hyper | (15, 15) | (15, 20) |
| d5_t3a | D5 Lower B | Poliquin Step-up | (8, 10) | (8, 12) |
| d5_t3c | D5 Lower B | Cable Tibialis Raise | (12, 15) | (10, 15) |
| d6_g2b | D6 Weak Points | DB Seal Row | (10, 12) | (8, 12) |
| d6_g3a | D6 Weak Points | Face Pull | (12, 15) | (10, 15) |
| d6_g3c | D6 Weak Points | T-Bar Row - Wide | (8, 10) | (8, 12) |

**Do NOT touch any other slot** — every slot NOT in this table (anchors like Bench Press/Belt Squat/RDL, HT-composite movements, assisted/incline movements, Pull-up, Dragon Flag, Ab Wheel, Face-Up Incline Knee Raise, Reverse Hyper's OTHER instances that use a different scheme like `REP_AT_CAP`/`FIXED`, etc.) is intentionally exempt from this standardization and must keep its current rep range exactly as-is.

## File targets
- Modify: `ironlog/generation/program_seed.py` — find each of the 19 `_add_te(...)` calls (search by `slot_id=` string literal for each of the 19 slot_ids above) and update its `rep_low=`/`rep_high=` kwargs to the new values.
- Modify: `docs/program/phase1-seed-source.yaml` — find the corresponding yaml entries (search by the movement's yaml key, e.g. `pendlay_row_narrow_d1` or whatever key naming convention this file uses per day — read the file's existing structure for D1/D2/D4/D5/D6 to find the right key per slot) and update their `reps: [low,high]` values to match.
- Modify: `tests/test_phase1_reconciliation.py` — this file's `CHANGED_REP_TARGETS` dict (or `UNCHANGED_REP_TARGETS` if any of the 19 slots happen to currently be listed there instead — check both) already has entries for at least some of these slot_ids (e.g. `d1_t3a` was already updated in spec 18) — add/update entries for all 19 slot_ids above with their new values. If a slot_id isn't in either dict yet, add it to `CHANGED_REP_TARGETS`.

## Edge cases
- **Verbatim mapping only** — do not apply any rounding/rule logic yourself, the exact 19 (slot_id, new_low, new_high) triples above are final and already applied to production; your job is purely to make the seed sources match them.
- **Every other slot_id must be completely untouched** — this is a narrow, 19-slot change; do not "helpfully" extend the standardization to any other slot not listed here, even if it looks like it might also fit the pattern.
- **`d1_t3a` (Pull-up) was already changed in spec 18** (6-10 → 8-12) — do not touch it again or list it in this spec's diff; it's a different, already-completed change.

## Dependencies
None — standalone, no schema change (the live DB mutation is already done and is not part of this diff), no HUMAN GATE required (this is a code/docs/test sync of an already-executed, already-confirmed data change, same precedent as spec 18).

## Verification
- Full server suite green: `.venv/bin/pytest -q` (baseline 526 passing before this change) — pay particular attention to `tests/test_phase1_reconciliation.py` and `tests/test_program_seed_yaml_parity.py`, both of which assert seed-source consistency and will catch any of the 19 values being wrong or any non-target slot being accidentally touched.
