# Task 3 Report — Reconcile program_seed to the authoritative YAML (D1-D6)

**Status:** DONE_WITH_CONCERNS (all green; one scope-expansion beyond the brief's explicit list)
**Branch:** `feat/config-seed-golive`
**Commit:** `5a2acab`
**Full suite:** `438 passed, 818 warnings in ~10.5s` (warnings all pre-existing datetime-deprecation; 0 failures)

> Note: this file previously held a stale, unrelated "note-apply redesign" task-3 report; overwritten per the Task-3 report contract for the config-seed go-live.

---

## Per-day summary of what changed (`ironlog/generation/program_seed.py`)

Every slot cross-checked against `docs/program/phase1-seed-source.yaml`.

### PROGRAM_TO_LIBRARY (4 new entries)
- `"DB Rear Delt Fly"` → `Rear Delt Fly [DB]`
- `"Reverse Nordic (assisted)"` → `Reverse Nordic Curl [GHR]`
- `"Face Pull"` → `Face Pull [FT]`
- `"Scout Reverse Hyper - Single Leg"` → `Reverse Hyper - Single Leg [REV_HYPER]`

### `_add_mr` signature
Extended to accept optional `rep_low`/`rep_high` and set them on the `MesoRotation` row (used by the new D5 single-leg meso-2 override).

### D1 Upper Push
- `d1_t1` bench `rep_low` 8→6.
- T3 GS tier `rest_seconds` 60→75.
- `d1_t3a` pull-up reps (8,8)→(6,10).
- Swapped slot-3 movements: `d1_t3c` now Lat Prayer (pattern `lat`), `d1_t4c` now Cross-Body Rear Delt Fly (pattern `rear_delt`); both reps (12,12) (they were reversed).

### D2 Lower A
- `d2_t1` belt squat `rep_low` 5→6.
- T1b tier `rest_seconds` 120→150; `d2_t1b` HT `tier_role` semi→anchor.
- `d2_t2a` nordic reps (6,10)→(8,8); `d2_t2b` scout `rep_high` 25→15.
- T3 (PAIR) tier `rest_seconds` 60→75, `rounds` 1→3.

### D4 Upper Pull (restructure)
- T1 tier `rest_seconds` 120→180; `d4_t1` pull-up `rep_low` 5→6.
- T2 GS: `d4_t2a` Meadows Row `rep_low` 8→10 (keeps meso-2 → Pendlay Row - Medium); `d4_t2b` now standalone Single-Arm DB Row (12,12); `d4_t2c` knee-raise `rep_low` 8→12.
- Removed old `d4_t3b` Meadows SA Row + its `_add_mr(..., "Single-Arm DB Row")` (that movement is now the standalone T2 slot).
- T3 GS tier `rest_seconds` 60→75: `d4_t3a` now DB Rear Delt Fly (12,12); `d4_t3b` now Andreoni Cable Pullover moved here (12,12, pattern `lat`); `d4_t3c` Dragon Flag unchanged (3,6).

### D5 Lower B
- T1 tier `rest_seconds` 120→180; `d5_t1` RDL reps (4,6)→(6,8) (keeps meso-2 → Staggered RDL).
- T1b tier `rest_seconds` 120→150; `d5_t1b` HT `tier_role` semi→anchor.
- `d5_t2b` scout bilateral reps (12,15)→(15,15); **added meso-2** rotation → Reverse Hyper - Single Leg with a 12–15 rep override (captured the `_add_te` return; replaced the stale "intentionally NO MesoRotation" comment).
- `d5_t2c` nordic reps (5,8)→(8,10).
- `d5_t3b` Sissy Squat (SISSY, 8,12) → Reverse Nordic (assisted) (`KneeModality.KOT`, 8,10, scheme `ASSISTED`).
- `d5_t3d` calf `rep_high` 12→15.

### D6 Weak Points (restructure)
- `d6_g1b` dips reps (5,8)→(8,12).
- GS2: `d6_g2a` now Reverse Hyper Recovery moved from GS3 (reverse_hyper, 15-20, `rpe_cap=6.0`, `tier_role=free`, FIXED); `d6_g2b` DB Seal Row unchanged (10,12); `d6_g2c` Lateral Raise `rep_low` 12→10.
- GS3: `d6_g3a` now Face Pull (12,15, pattern `rear_delt`, DOUBLE_PROGRESSION, replaces dropped Cross-Body Rear Delt Fly); `d6_g3b` Cable V-Bar Pushdown unchanged (8,12, SINGLE_SESSION); `d6_g3c` now T-Bar Row Wide moved from GS2 (horizontal_pull, 8,10, semi).

### YAML annotations (Step 6, `docs/program/phase1-seed-source.yaml`)
- d2 `belt_squat`: added `meso: {2: back_squat}`.
- d4 `meadows_row_bruno_bar`: added `meso: {2: pendlay_row}`.

---

## Parity-test design (`tests/test_program_seed_yaml_parity.py` — the keystone)

Three authoritative tests, all pass by construction after the edits:

1. **`test_seeded_base_slots_match_yaml`** — per training day, flattens the seeded program in (tier_order, exercise_order) and compares `(resolved Movement.name, rep_low, rep_high)` **positionally** against the YAML base slots.
2. **`test_seeded_tiers_match_yaml`** — per day, compares `(tier_label, rest_seconds, rounds, shoe)` in tier order against the YAML tiers (`group_key`, `rest`, `rounds` default 1, `shoe`). Independently pins the now-non-uniform per-day rests.
3. **`test_seeded_meso_rotations_match_yaml`** — compares the **set** of seeded `MesoRotation (name, rep_low, rep_high)` against the YAML-derived meso-2 set (inline `meso: {2: ...}` dicts + the standalone `meso: 2` single-leg entry with its rep override).

Design decisions that make it genuinely anti-drift (not a rubber stamp):
- The test carries its **own** explicit `YAML_M_TO_LIBRARY` map (YAML m-id → canonical library Movement.name) plus `MESO_VARIANT_TO_LIBRARY`, resolving by library name **independently** of the seeder's own `PROGRAM_TO_LIBRARY` — a bad seeder mapping cannot mask a drift.
- A YAML ex entry with scalar `meso: 2` is a meso-2 rotation (excluded from base slots, checked in test 3); `meso: {..}` dicts and `meso: 1` scalars are base slots. This correctly handles the D5 T2 four-entry tier (3 base slots + 1 single-leg rotation) vs 3 seeded TierExercises.
- YAML-only fields (load/assist/ht_*/unilateral/pattern/rule) intentionally skipped — baseline/Movement-layer, not DEFINITION-row fields.

TDD confirmed: written first, all 3 failed loudly against the drifted seeder; green only after Steps 1–6.

---

## Ancillary tests + reconciliation updated (stale-structure reconciliation)

The restructure invalidated assertions pinning the OLD drifted structure. Per "YAML wins", updated:

- `tests/test_program_seed_rotation_guard.py` — replaced the removed `d4_t3b → Single-Arm DB Row` meso assertion with the new `d5_t2b → Reverse Hyper - Single Leg` (incl. the 12–15 rep-override assertion). `d5_t1 → Staggered RDL` and the halt-and-flag guard test unchanged.
- `tests/test_program_seed.py` — dropped the `KneeModality.SISSY >= 1` assertion (Sissy no longer programmed; D5 T3 slot-2 is now Reverse Nordic / KOT). TIB/NORDIC/KOT frequencies still hold.
- `tests/test_assembler_fidelity.py` — bench-anchor reps 8/8→6/8; Reverse-Hyper-Recovery group rest 60→90 (moved GS3→GS2); docstrings updated.
- `tests/test_phase1_reconciliation.py` + `scripts/reconcile_phase1.py` — updated rep targets to final YAML values, moved the Reverse-Hyper rpe_cap from `d6_g3c`→`d6_g2a`, and **re-keyed tier rests from `tier_label` to `(day_role, tier_label)`** because T1 (D1/D2=120 vs D4/D5=180) and T3 GS (D1/D4=75 vs D5=60) are no longer uniform per label. Regenerated `deploy/migrations/013_phase1_reconciliation.sql` via the script's own `write_migration_sql()` path (tier-rest UPDATEs now scope by a `programday` subquery).

---

## Where the YAML was ambiguous / how resolved
- None materially ambiguous. Every slot cross-checked. Movement identities resolved by exact library name (all confirmed present in `ironlog/seed.py`). `KneeModality.KOT` used for Reverse Nordic per the brief (enum has NORDIC/TIB/KOT/SISSY).

## Concerns
1. **Scope beyond the brief's explicit "update" list.** The brief anticipated only D6 reconciliation changes, but the D1/D2/D4/D5 rep + tier-rest changes also invalidated `test_program_seed.py` (SISSY), `test_assembler_fidelity.py` (bench reps, RH rest), and the reconciliation's uniform-per-label tier-rest model. All updated to the YAML-correct state to keep the suite green; every change is YAML-driven, not a loosening of assertions.
2. **`reconcile_phase1.py` applied against a live DB seeded from the OLD source.** Running the script now writes e.g. rpe_cap to `d6_g2a` on a DB whose slots still hold pre-restructure movements — semantically stale for that specific pre-existing live DB. Moot for go-live: the plan is a full reseed (script/migration then become idempotent no-ops), and `ironlog.db` is untracked so no git artifact was produced. Flagged in case an interim live DB is used before the reseed.
3. **Scheme fidelity not parity-checked.** `d5_t3b` Reverse Nordic uses scheme `"ASSISTED"` per the brief; the parity test does not assert `scheme` (per the brief's skip-non-definition guidance), so scheme fidelity rests on the brief + review, not the keystone.

---

## Task 3 Report (STAB maintenance-block redesign): D4 — Upper Pull + Vertical Press

**This is a DIFFERENT "Task 3" from the config-seed-golive chunk above** — that
task-numbering restarted with the 2026-08-10 STAB maintenance-block redesign
plan (`docs/superpowers/plans/2026-08-10-stab-maintenance-block-redesign.md`).
Per this repo's established pattern of appending fix-wave/new-chunk sections
to the same `task-N-report.md` rather than creating a new file per
task-numbering restart (matches Task 2's own append of its note-apply-
redesign section onto `task-2-report.md`), this section is appended below
the prior, unrelated Task 3 content rather than overwriting it.

Status: **DONE**
Branch: `feat/stab-d4-upper-pull` (worktree `~/projects/IronLog-V2-wt-stab-d4`, removed after merge)
Commit: `8fecbf4` (merged to `main` via fast-forward, no rebase needed — `main` was at the
exact fork point `5507fca`)
Timestamp: 2026-08-11

### Objective
Reconcile D4 Upper Pull's `TierExercise` wiring, seed YAML, rule mapping, and
`MovementState` baselines to match
`docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md`'s D4
session (Task 3 of 7 in the STAB maintenance-block redesign plan).

### Ambiguity resolutions (both flagged explicitly by the brief)

1. **`PureTorque Pro Rotation` vs a new "Cable Woodchopper" movement.**
   Verified via `grep -n "PureTorque Pro Rotation" ironlog/seed.py`: the
   movement already exists, already wired at D4's `d4_t3d` (2026-07-26,
   replacing Dragon Flag), with equipment `[ares_high_pulley, puretorque_pro]`
   — identical to the FINAL doc's `cable_woodchopper` entry. **Reused as-is,
   no rewiring.** (Live production had already drifted this slot's reps to
   3-6/scheme=None vs the dev-seed's 8-12/DOUBLE_PROGRESSION — pre-existing,
   unrelated to this task, left untouched in both dev and production per the
   "unchanged" instruction; see Issues & Decisions.)

2. **`lying_tricep_extension_camber_7` — reuse `Lying Tricep Extension [SB]`
   or create a new grip-specific movement?** This required a full reversal
   mid-task. Initial analysis (and a first advisor consult) leaned toward a
   NEW movement, reasoning from the EZ-Bar-Curl grip-variant-per-row
   precedent and a surface reading of the FINAL doc's "3 grips, 1 bar" note
   (line 935). Direct inspection of
   `docs/superpowers/plans/2026-08-10-stab-maintenance-block-redesign.md`
   (Task 1's own `_seed_d1` rewrite, line ~123) surfaced the actual
   controlling precedent: D1's Bench Press carries an identical camber-bar
   "21\" grip" annotation, and the plan owner's own comment there states
   explicitly — "physical-setup detail, not a schema field, same
   movement/load_code as before." That is a direct, already-decided
   precedent for this exact camber-bar family, and it says grip notes on
   this family do NOT by themselves require a new movement. The EZ-curl
   precedent applies only when NAMED grip variants coexist and need
   disambiguation (Medium/Narrow/Wide, all simultaneously live); D4's
   tricep-extension row has no grip in its name/tags and no sibling variant
   anywhere in the 7-task plan. **Resolved: REUSE `Lying Tricep Extension
   [SB]`** — a dated comment was added to that Movement row in
   `ironlog/seed.py` documenting the reuse and citing the Bench Press
   precedent. A second advisor consult confirmed this reversal against the
   fresh evidence before proceeding (see the session transcript — this is
   not asserted from memory, both consults are real, visible calls).
   Flagging this prominently per the advisor's explicit recommendation,
   since the task's own dispatch instructions leaned NEW: the plan owner
   should feel free to veto this at review without it being a blocking
   round-trip — the athlete-visible difference is a display name, and no
   downstream logic consumes movement identity here beyond display
   (`MovementState` is day-scoped, so D4 gets a fresh needs-cal state at its
   own new slot either way).

### A third discrepancy caught by direct verification (not escalated — brief error)

`task-3-brief.md`'s Step 2 dict for `"Stryker Pad CSR Barbell"` specifies
`load_code="OB"` (Gladiator WL bar). Cross-checking the FINAL doc's own
equipment line (`equipment: [stryker_pad, apex_bench, black_diamond_dbd]`)
against `ironlog/seed.py`'s `CODE_TO_EQUIP` map shows `black_diamond_dbd` is
`"PB"` (Double Black Diamond), not `"OB"`. Corrected to `[PB]` in both the
Movement row and the deploy script. Logged here and in the merge commit
message rather than escalated — mechanically verifiable from data already in
the repo, same class of error as Task 1/Task 2's own caught brief errors.

### What changed (full detail in the merge commit message, `git log -1 8fecbf4`)

- `ironlog/seed.py`: 5 new movements (Seated BTN OHP [PB], Better Fly Lat
  Pulldown [FT], Stryker Pad CSR Barbell [PB], Better Fly Cable Pullover
  [FT], Ab Trainer Hanging Leg Raise). Dated reuse comment added to `Lying
  Tricep Extension [SB]`.
- `ironlog/generation/program_seed.py`: `_seed_d4` rewritten in full. T1
  Standing OHP [PB] → Seated BTN OHP [PB] (fresh slot `d4_t1_btn_ohp`, NOT a
  reuse of the old `d4_t1_ohp` — this is a genuinely different movement
  filling a vacated anchor, not the one explicitly-allowed reassignment
  case). T1b Wide-Grip Pull-up [TOWER] → Better Fly Lat Pulldown [FT]
  (reuses slot_id `d4_t1` — the ONE allowed reassignment case per the
  brief, same treatment as D1's T1b precedent). T2 GS fully turned over
  (fresh slots `d4_t2d/e/f`) — Meadows Row, Single-Arm DB Row, Face-Up
  Incline Knee Raise all drop out of D4 entirely, along with Meadows Row's
  meso-2 rotation to Pendlay Row (dropped, not carried to any new slot).
  T3 GS: DB Rear Delt Fly widens 8-12→10-15 reps (unchanged slot `d4_t3a`),
  Andreoni Cable Pullover drops out replaced by the reused Lying Tricep
  Extension [SB] (fresh slot `d4_t3e`), PureTorque Pro Rotation (`d4_t3d`)
  completely unchanged.
- `docs/program/phase1-seed-source.yaml`: `d4:` block rewritten to match.
- `ironlog/generation/rule_wiring.py` + its test copy
  (`tests/test_program_seed_yaml_parity.py`): new `m:` ids for the 5 new
  movements plus `lying_tricep_extension_camber7_d4` → `Lying Tricep
  Extension [SB]`.
- `ironlog/generation/baseline_seed.py`: `d4_t2a/b/c` and `d4_t3b` BASELINES
  entries removed (slots vacated); no entries added for new slots
  (needs-calibration is correct).

### Test fallout (full reasoning in the merge commit message)

701 passed, matching the stated baseline exactly. Fallout beyond the
brief's declared scope, all direct/foreseeable consequences of the T1/T1b/T2
turnover:
- `tests/test_generation_context.py`,`tests/test_slot_override_skeleton.py`,
  2 of `tests/test_generation_fallback.py`'s reorder tests — repointed from
  D4's now-retired `d4_t2a` meso-rotation example to D5's `d5_t2b` (the
  program's other adaptive-slot meso-rotation example, unaffected).
- `tests/test_generation_fallback.py`'s 3rd reorder test — slot_ids updated
  `d4_t2a/b/c` → `d4_t2d/e/f`, stays on D4 (no meso-rotation dependency).
- `tests/test_ramp_sets.py` — the D4 non-ramp-eligible-anchor example
  repointed from the dropped Wide-Grip Pull-up to the new Better Fly Lat
  Pulldown anchor (same proof, same non-ramp-eligible class).
- `tests/test_rule_wiring.py` — INCLINE_REDUCTION spot-check removed
  (movement family now fully unwired program-wide, matches the existing
  BODY_POSITION-is-unused precedent in the same dict).
- **`tests/test_knee_raise_incline.py` + `test_knee_raise_retype_migration.py`**
  — the largest fallout. Face-Up Incline Knee Raise drops out of D4 (it was
  already dropped from D1 in Task 1), making it fully unwired program-wide;
  `Movement.progression_rule` for it is now `None` (no YAML entry uses
  `rule: incline_reduction` anymore). Four tests built around its REAL live
  wiring were inverted/retired rather than deleted:
  - baseline-seeding test inverted to assert NO MovementState is (re)seeded
    for it on any day;
  - the direct `advance()`-dispatch test keeps its rule-logic proof (the
    rule is passed as an explicit argument, not read from the Movement row)
    but drops the now-false wiring assertion;
  - the "no lb load leaks through generation" regression test retired/
    inverted to "does not appear in the generated D4 session" — the
    ASSISTED→assist_level routing seam it proved is still exercised live by
    `test_golive_phase1.py::test_d6_dips_resolves_seeded_assist_level`
    (Dips, D6);
  - the end-to-end `run_analysis` test inverted to prove the engine
    correctly no-ops (assist_level stays put across two clean sessions) on
    an unwired movement, since `run_analysis`'s dispatch is keyed on
    `Movement.progression_rule`, confirmed by reading
    `ironlog/persistence/run_analysis.py` directly (this contradicts one
    detail of the advisor's suggested fix — the advisor assumed a manually
    inserted `MovementState` would still let the engine advance; it does
    not, because dispatch depends on the Movement-level rule, not just
    state presence — corrected after independent verification via grep,
    per this repo's "verify, don't trust" discipline).
  - `test_knee_raise_retype_migration.py`'s three baseline-dependent tests
    now insert the D4 `MovementState` directly (new
    `_seed_d4_knee_raise_state` helper), since `seed_movement_baselines` no
    longer creates it.
- `tests/test_phase1_reconciliation.py`, `tests/test_library_seed.py` —
  rep-target and library-count expectations updated (+5 movements,
  122→127; ACTIVE 115→120).
- `scripts/build_hgc_condensed_week.py` — the 7/29 D4 mini-session's
  movement list repointed to movements that actually exist in D4's current
  live-regenerated wiring (this script re-derives via a fresh
  `generate_session` call, so it is NOT a frozen historical record — see
  its own file-level comment).

### Production deployment

Pre-flight:
- `ssh myflix "systemctl is-active ironlogv2"` → `active`.
- Active-use check: `journalctl -u ironlogv2 --since "30 min ago"` → no
  entries. Direct DB check for any `Session` row dated 2026-08-10 or later →
  none. No in-progress-athlete-use conflict.
- Confirmed `main` (`~/projects/IronLog-V2`) at `5507fca`, identical to this
  branch's fork point — no rebase needed. `git merge --ff-only
  feat/stab-d4-upper-pull` → clean fast-forward to `8fecbf4`.
- Full suite re-run on the production checkout post-merge (unrelated local
  uncommitted changes exist there from other in-progress work —
  `.specs/routing-plan.md`, other task reports, `docs/build-plan.md`,
  various untracked `.specs/*.md` and `ironlog.db.bak-*` files — none
  overlap this task's files): **701 passed**.
- Backup: `cp ironlog.db ironlog.db.bak-task3-20260811-211413` on the
  production checkout before any write.

Applied via a one-off, idempotent Python/SQLModel script
(`_deploy_task3_d4.py`, not committed, run then deleted), mirroring Task 2's
`_deploy_task2_d2.py` pattern — run from the worktree's own directory with
the production checkout's `.venv` interpreter, against the production DB
file via an absolute `sqlite:////home/jstout/projects/IronLog-V2/ironlog.db`
URL. Script output:
```
CREATED movement: Seated BTN OHP [PB] (id=128)
CREATED movement: Better Fly Lat Pulldown [FT] (id=129)
CREATED movement: Stryker Pad CSR Barbell [PB] (id=130)
CREATED movement: Better Fly Cable Pullover [FT] (id=131)
CREATED movement: Ab Trainer Hanging Leg Raise (id=132)
DELETED d4_t1_ohp TierExercise (id=43, Standing OHP [PB])
CREATED d4_t1_btn_ohp TierExercise (Seated BTN OHP [PB])
UPDATED d4_t1 (id=17) -> Better Fly Lat Pulldown [FT], scheme=DOUBLE_PROGRESSION
DELETED 1 MesoRotation row(s) for d4_t2a
DELETED d4_t2a TierExercise (id=18)
DELETED d4_t2b TierExercise (id=19)
DELETED d4_t2c TierExercise (id=20)
CREATED d4_t2d TierExercise (Stryker Pad CSR Barbell [PB])
CREATED d4_t2e TierExercise (Ab Trainer Hanging Leg Raise)
CREATED d4_t2f TierExercise (Better Fly Cable Pullover [FT])
UPDATED d4_t3a (id=21, Rear Delt Fly [DB]) reps -> 10-15
DELETED d4_t3b TierExercise (id=22, Andreoni Cable Pullover)
CREATED d4_t3e TierExercise (Lying Tricep Extension [SB], reused movement)
UNTOUCHED d4_t3d (id=23, PureTorque Pro Rotation) -- live reps=3-6, scheme=None
  (diverged from dev-seed 8-12/DOUBLE_PROGRESSION pre-existing this task,
  out of scope, left as-is)

--- pending_load_delta sweep (D4-touched movements) ---
  Rear Delt Fly [DB] / D4: pending_load_delta=None

Task 3 D4 deploy script complete.
```
Ran `rule_wiring.wire_progression_rules()` against production:
`{'changed': 5, 'total': 41}` (the 5 new movements). Verified the resulting
DB structure directly — matches the target table exactly (tier
order/rest/rep/scheme all correct; `d4_t3d` confirmed untouched).

### Live verification: real `generate_session("D4 Upper Pull", ...)` against production

**Pass 1 — direct DB script, before restart:**
```
T1:  Seated BTN OHP [PB]            reps=(4,6)   load=None (needs-cal)
T1b: Better Fly Lat Pulldown [FT]   reps=(6,8)   load=None (needs-cal)
T2 GS: Stryker Pad CSR Barbell [PB] reps=(8,12)  load=None (needs-cal)
       Ab Trainer Hanging Leg Raise reps=(8,12)  load=None (needs-cal)
       Better Fly Cable Pullover    reps=(10,15) load=None (needs-cal)
T3 GS: Rear Delt Fly [DB]           reps=(10,15) load=15.0 (real progressed load, carried forward)
       Lying Tricep Extension [SB]  reps=(8,12)  load=None (needs-cal)
       PureTorque Pro Rotation      reps=(3,6)   load=None (untouched, pre-existing live drift)
```
Assembled: True, rejections: [].

**Pass 2 — real `POST /generate` through the RUNNING service, after restart**
(the actual completion-criteria smoke call):
```
sudo systemctl restart ironlogv2 && curl -s http://localhost:8000/docs -> 200
curl -s -X POST http://localhost:8000/generate -d '{"day_role": "D4 Upper Pull"}'
```
Response `preview.groups` (tier label / rest / movement names):
```
-- T1 (STRAIGHT) rest=120 --
   Seated BTN OHP [PB]
-- T1b (STRAIGHT) rest=180 --
   Better Fly Lat Pulldown [FT]
-- T2 GS (GIANT_SET) rest=90 --
   Stryker Pad CSR Barbell [PB]
   Ab Trainer Hanging Leg Raise
   Better Fly Cable Pullover [FT]
-- T3 GS (GIANT_SET) rest=75 --
   Rear Delt Fly [DB]
   Lying Tricep Extension [SB]
   PureTorque Pro Rotation
```
Matches the target structure exactly, live, through the running service.
(No `/health` route exists on this service — `/docs` used as the up-check,
matching the actual available endpoint.)

### Merge

`git merge --ff-only feat/stab-d4-upper-pull` on the production checkout —
clean fast-forward, no conflicts (main was at the exact fork point). Worktree
removed (`git worktree remove -f`) and branch deleted (`git branch -D
feat/stab-d4-upper-pull`) after a successful, verified merge.

### Deploy Gate

Class 1 (code-only restart, additive/definition-row changes only, no
schema/data migration): `sudo systemctl restart ironlogv2` → up-check
(`/docs` → 200) → smoke call: real `generate_session("D4 Upper Pull", ...)`
through the running service, confirming the newly-deployed structure.
**DEPLOYED.**

### Issues & Decisions

- **`pending_load_delta` sweep**: scoped to movements staying wired on D4
  (Rear Delt Fly, plus the new/reused movements, which have no prior
  MovementState at all). Found: none stale on anything staying wired.
  Separately found (not cleared, logged for visibility): Meadows Row [OB +
  LM]'s D4-scoped MovementState carries `pending_load_delta=1.25` — this
  slot is being fully unwired by this task (never-delete-orphans), so the
  stale delta no longer affects any future prescription; left in place
  rather than cleared, since clearing an orphaned row's state isn't this
  task's concern and the row is harmless once nothing reads it.
- **`d4_t3d` (PureTorque Pro Rotation) production drift, found not fixed.**
  Live production's rep range (3-6) and `TierExercise.scheme` (`None`)
  diverge from the dev-seed/FINAL-doc values (8-12, DOUBLE_PROGRESSION) —
  this predates this task (not introduced by Task 3) and the brief
  explicitly frames this slot as "unchanged, no rewiring needed." Left
  untouched in both dev-seed code and the production deploy script,
  consistent with that instruction. Flagged here as a genuine, pre-existing
  discrepancy between the FINAL doc / seed source and live production
  reality, for the plan owner's awareness — out of this task's scope to
  reconcile.
- **Two advisor consults, one reversed.** The first (before any file edits,
  after full orientation reading) confirmed the plan and caught the
  DB-Rear-Delt-Fly rep-range oversight and the T1b scheme-shape point. A
  second consult, prompted by finding the D1 Bench Press "physical-setup
  detail" precedent in the plan doc (contradicting the first consult's
  "NEW movement" lean on the tricep-extension question), explicitly
  reversed that earlier advice — logged above under Ambiguity resolution
  #2, per the standing instruction to surface (not silently switch on) a
  primary-source/advisor conflict.

### Rollback

- `git revert 8fecbf4` in the IronLog-V2 repo (code).
- DB: `cp ironlog.db.bak-task3-20260811-211413 ironlog.db` on the production
  checkout, then `sudo systemctl restart ironlogv2` (restores the
  pre-Task-3 D4 wiring, including the pre-existing Meadows Row
  `pending_load_delta=1.25`, which this task never touched either way).

### Open items

- D4's now-orphaned `MovementState` rows (Meadows Row, Single-Arm DB Row,
  Face-Up Incline Knee Raise, Andreoni Cable Pullover, the old Standing
  OHP row) are left in place per the never-delete-orphans convention — no
  action needed, just noting their existence for anyone auditing
  `MovementState` row counts later.
- `d4_t3d`'s production drift (above) is unresolved and out of this task's
  scope — worth a dedicated look in a future session if the athlete's real
  PureTorque Pro Rotation prescription (3-6 reps) is intentional and should
  be reflected back into the FINAL doc / seed source, or if it was an
  unintentional drift that should be corrected to 8-12.

### Hand-off
Ready for: Task 4 (D5).
