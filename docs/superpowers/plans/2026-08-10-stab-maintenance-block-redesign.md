# STAB Maintenance Block Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the live IronLog-V2 program (all 5 training days) with the athlete's finalized maintenance-block redesign, seed D1's real Wk1 baselines, and flip the engine phase CUT → STAB.

**Architecture:** Pure seed/config-data work — no new engine logic, no schema migration. Each training day is edited in lockstep across `ironlog/seed.py` (movement library), `ironlog/generation/program_seed.py` (TierExercise wiring), `docs/program/phase1-seed-source.yaml` (source-of-truth YAML), `ironlog/generation/rule_wiring.py` (progression-rule mapping), and `ironlog/generation/baseline_seed.py` (calibrated baselines), then applied directly to the live production DB via SSH + SQLModel scripts (mirrors this repo's established pattern all session — see commit history around Wide-Grip Pull-up, Pendlay Row T1b promotion, PureTorque Pro Rotation).

**Tech Stack:** Python, SQLModel, FastAPI, pytest.

## Global Constraints

- **NO `from __future__ import annotations`** in any file with `Relationship(...)` (project-wide, breaks SQLModel).
- **Never reassign a `slot_id` to a different movement** — established convention throughout this repo. A movement that moves tiers keeps its `slot_id`; a genuinely new movement in a vacated spot gets a new `slot_id`.
- **Orphaned `MovementState`/`HtProgressionState` rows are left in place**, not deleted, when a movement is retired.
- Full test suite must stay green (baseline: 701 passing) after every task.
- Every live production change is verified via a direct `generate_session` smoke test before moving to the next task — never trust "the migration ran," always confirm the assembled output.
- `sudo systemctl restart ironlogv2` after every live DB content change that touches code (seed.py/program_seed.py/etc. changes require a restart to take effect; pure data-only DB writes via a one-off script do not, but this plan restarts after each day anyway for consistency since code always changes too).
- Design source: `docs/superpowers/specs/2026-08-10-stab-maintenance-block-redesign-design.md`. Content source: `docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md`.

---

## Task 1: D1 — reconcile to already-executed reality

**Files:**
- Modify: `ironlog/seed.py` (new movement: Better Fly Standing Lateral Raise)
- Modify: `ironlog/generation/program_seed.py:35-44` (PROGRAM_TO_LIBRARY), `:425-465` (`_seed_d1`)
- Modify: `docs/program/phase1-seed-source.yaml` (d1 block)
- Modify: `ironlog/generation/rule_wiring.py` (YAML_M_TO_LIBRARY)
- Modify: `ironlog/generation/baseline_seed.py` (BASELINES)
- Test: `tests/test_library_seed.py`, `tests/test_program_seed_yaml_parity.py`, `tests/test_golive_phase1.py`, `tests/test_rule_wiring.py`, `tests/test_generation_skeleton.py`, `tests/test_generation_assembler.py`

**Interfaces:**
- Consumes: existing `Pull-up [TOWER + TUBES]`, `Wide-Grip Pull-up [TOWER]`, `Lat Prayer [ANDREONI + FT]`, `Ab Wheel [WHEEL]`, `Bench Press [PB]`, `Pendlay Row - Narrow [OB]`, `Lying Tricep Extension [SB]` movements — all already exist, no new rows for these.
- Produces: new movement `Better Fly Standing Lateral Raise [FT]` (library name others may reference).

### Target D1 structure (from the FINAL source doc)

| Tier | Slot | Movement | Reps | Rule |
|---|---|---|---|---|
| T1 | d1_t1 | Bench Press [PB] (camber 21" grip — equipment note only, same movement) | 4-6 | RPE_8_STANDARD |
| T1b | d1_t2a | Pendlay Row - Narrow [OB] | 4-6 | FIXED_LOAD (held @ 170) |
| T2 GS | d1_t2d | Lying Tricep Extension [SB] | 8-12 | RPE_8_STANDARD (unchanged) |
| T2 GS | (new) | Better Fly Standing Lateral Raise [FT] | 10-15 | RPE_8_STANDARD |
| T2 GS | d1_t2c | Face-Up Incline Knee Raise | 10-15 | INCLINE_REDUCTION (unchanged) |
| T3 GS | (movement swap) | Wide-Grip Pull-up [TOWER] | 4-6 | PULL_UP_ROLLING_MAX |
| T3 GS | d1_t3c | Lat Prayer [ANDREONI + FT] | 8-12 | RPE_8_STANDARD (unchanged) |
| T3 GS | (new) | Ab Wheel [WHEEL] | 8-12 | REP_LADDER |

Dropped from D1 entirely: `Incline DB Press [DB + BENCH]` (d1_t2b), `Cross-Body Lateral Raise [FT]` (d1_t2b... wait, `Cross-Body Lateral Raise` was `d1_t3b`) — replaced by Better Fly Standing Lateral Raise.

- [ ] **Step 1: Add the new movement to `ironlog/seed.py`**

Find the `Better Fly` / lateral-raise-family section (search for `"Cross-Body Cable Lateral Raise [FT]"` to locate the right neighborhood) and add a sibling entry:

```python
    dict(name="Better Fly Standing Lateral Raise [FT]", base_name="Better Fly Standing Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="FT", tags=["FT", "BETTER_FLY"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10,
         primary_muscle="SIDE_DELT", secondary_muscles=["FRONT_DELT"]),
```

- [ ] **Step 2: Update `ironlog/generation/program_seed.py`'s `PROGRAM_TO_LIBRARY`**

At line 38 (after `"Incline DB Press"` entry — leave that entry in place, it's still a valid library movement even though D1 stops using it):

```python
    "Better Fly Standing Lateral Raise":            "Better Fly Standing Lateral Raise [FT]",
```

- [ ] **Step 3: Rewrite `_seed_d1` (lines 425-465)**

```python
def _seed_d1(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Bench Press [PB] (anchor). 2026-08-10: global T1/T1b rep range
    # drop 6-8 -> 4-6 (maintenance block, athlete directive, real Wk1
    # execution locked 155x3x6 @ RPE8). Equipment note (Belle Mere BMF
    # Camber Bar, 21" grip) is a physical-setup detail, not a schema field
    # -- same movement, load_code unchanged.
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1.id, "d1_t1", "Bench Press [PB]", lib, 1, "anchor",
            pattern="bench", rep_low=4, rep_high=6, rpe_cap=8.0,
            scheme="STRAIGHT")

    # T1b — Pendlay Row Narrow (anchor). 2026-08-10: held at 170 while the
    # strain heals (real Wk1 executed 170x3x8, over the new 4-6 rep cap --
    # logged as-is, the hold is on load not on stopping mid-set). Rep range
    # drops 6-8 -> 4-6 alongside every other T1/T1b primary.
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1b.id, "d1_t2a", "Pendlay Row Narrow", lib, 1, "anchor",
            pattern="horizontal_pull", rep_low=4, rep_high=6,
            scheme="DOUBLE_PROGRESSION")

    # T2 GS — Lying Tricep Extension / Better Fly Standing Lateral Raise /
    # Face-Up Incline Knee Raise. 2026-08-10: Incline DB Press dropped,
    # Better Fly Standing Lateral Raise added (final maintenance-block
    # composition -- Ab Trainer Cable Crunch never landed here, D1's core
    # requirement is fully covered by Ab Wheel in T3 below).
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, t2.id, "d1_t2d", "Lying Tricep Extension", lib, 1, "free",
            pattern="tricep_extension", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2e", "Better Fly Standing Lateral Raise", lib, 2, "free",
            pattern="lateral_raise", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2c", "Face-Up Incline Knee Raise", lib, 3, "free",
            pattern="core", rep_low=10, rep_high=15)

    # T3 GS — Wide-Grip Pull-up (dead-hang) / Lat Prayer / Ab Wheel Rollout.
    # 2026-08-10: switched from assisted neutral-grip (Pull-up [TOWER +
    # TUBES]) to unassisted Wide-Grip dead-hang -- athlete directive, real
    # Wk1 executed 4/4/4. Cross-Body Lateral Raise dropped (Better Fly
    # Standing Lateral Raise in T2 above covers that role now). Ab Wheel
    # added -- D1's mandatory core slot (anti-extension pattern), kept
    # after the athlete confirmed proper bracing technique resolves the
    # earlier hyperextension-strain concern.
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=75, shoe="Metcon 9")
    _add_te(db, t3.id, "d1_t3a", "Wide-Grip Pull-up", lib, 1, "free",
            pattern="vertical_pull", rep_low=4, rep_high=6, scheme="REP_RATIO")
    _add_te(db, t3.id, "d1_t3c", "Lat Prayer", lib, 2, "free",
            pattern="lat", rep_low=8, rep_high=12, scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d1_t3d", "Ab Wheel Rollout", lib, 3, "free",
            pattern="core", rep_low=8, rep_high=12, scheme="REP_LADDER")
```

Note: `d1_t3a`'s `movement_id` resolves to a DIFFERENT movement now (`Wide-Grip Pull-up [TOWER]` instead of `Pull-up [TOWER + TUBES]`) while KEEPING the same `slot_id` — this is allowed because it's the same real-world "D1's pull-up slot," not a slot being handed to an unrelated movement (mirrors this session's established distinction: reassigning a slot's movement when the athlete explicitly changes what goes in that conceptual position is fine; reusing a vacated slot_id for a totally different exercise is not — that's why Ab Wheel gets a fresh `d1_t3d`, not `d1_t3b`/`d1_t4b`).

`Cross-Body Lateral Raise [FT]` (`d1_t3b`) and `Incline DB Press [DB + BENCH]` (`d1_t2b`) both fall out of the program with this rewrite — no explicit orphan handling needed beyond removing their `_add_te` calls (they stay defined in the library, just unwired from D1).

- [ ] **Step 4: Update `docs/program/phase1-seed-source.yaml`'s `d1:` block**

Find the `d1:` section and replace it entirely:

```yaml
  d1:  # Upper A / Push — shoe: Metcon 9 (no swap)
    T1_STRAIGHT: {group_key: "T1", rest: 120, shoe: "Metcon 9", anchor: true, ex: [
      {m: bench_press, reps: [4,6], rule: rpe_8_standard, load: 155}]}  # 2026-08-10: maintenance block, T1 rep range 6-8 -> 4-6, real Wk1 locked 155x3x6 RPE8 (Belle Mere BMF Camber Bar 21" grip)
    T1b: {group_key: "T1b", rest: 120, shoe: "Metcon 9", anchor: true, ex: [
      {m: pendlay_row_narrow, reps: [4,6], rule: hold_load_strain_constraint, load: 170}]}  # 2026-08-10: held at 170 while strain heals (maps to FIXED_LOAD), rep range 6-8 -> 4-6
    T2_GIANT: {group_key: "T2 GS", rest: 90, rounds: 3, shoe: "Metcon 9", ex: [
      {m: lying_tricep_extension_d1, reps: [8,12], rule: rpe_8_standard},
      {m: better_fly_standing_lateral_raise_d1, reps: [10,15], rule: rpe_8_standard, load: 20},  # 2026-08-10: real Wk1 locked 20x3x12 RPE8
      {m: face_up_incline_knee_raise_d1, reps: [10,15], rule: incline_reduction, assist_level: 25, assist_ladder: [25,20,15,10,5,0]}]}
    T3_GIANT: {group_key: "T3 GS", rest: 75, rounds: 3, shoe: "Metcon 9", ex: [
      {m: pull_up_d1, reps: [4,6], rule: pull_up_rolling_max},  # 2026-08-10: switched to Wide-Grip dead-hang unassisted (was assisted neutral-grip 3-band ladder), real Wk1 executed 4/4/4
      {m: lat_prayer, reps: [8,12], rule: rpe_8_standard, load: 70},  # 2026-08-10: real Wk1 locked 70x3x12 (flagged under-loaded, RPE 6-7 -- Wk2 jump to 85-95 is a live in-session decision, not seeded)
      {m: ab_wheel_rollout_d1, reps: [8,12], rule: rep_ladder_at_cap}]}  # 2026-08-10: D1's mandatory core slot (anti-extension), real Wk1 locked 3x8 bodyweight
```

- [ ] **Step 5: Update `ironlog/generation/rule_wiring.py`'s `YAML_M_TO_LIBRARY`**

The `pull_up_d1` entry already exists pointing at `"Pull-up [TOWER + TUBES]"` — change it, and add the two new `m:` ids from the yaml block above:

```python
    "pull_up_d1":                        "Wide-Grip Pull-up [TOWER]",       # was "Pull-up [TOWER + TUBES]"
    "better_fly_standing_lateral_raise_d1": "Better Fly Standing Lateral Raise [FT]",
    "ab_wheel_rollout_d1":               "Ab Wheel [WHEEL]",
```

Add `"rep_ladder_at_cap": ProgressionRule.REP_LADDER` and `"hold_load_strain_constraint": ProgressionRule.FIXED_LOAD` to `RULE_STRING_TO_ENUM` if not already present (check first — `rep_ladder` already maps to `REP_LADDER`; confirm whether `rep_ladder_at_cap` is a new string key needing its own entry, since it's spelled differently from the existing `"rep_ladder"` key).

- [ ] **Step 6: Update `tests/test_program_seed_yaml_parity.py`'s own `YAML_M_TO_LIBRARY` copy**

Same three line changes as Step 5, in this file's separate copy (the anti-drift keystone test maintains its own mapping deliberately).

- [ ] **Step 7: Seed D1's real Wk1 baselines in `ironlog/generation/baseline_seed.py`**

Update existing `BASELINES` entries and add the new ones for D1's slots:

```python
    "d1_t1": ("load", 155, None),          # was 165 -- real Wk1 locked value
    "d1_t2a": ("load", 170, None),         # unchanged, held
    "d1_t2d": ("load", ?, None),           # Lying Tricep Extension -- check current value, not in source doc's Wk1 lock list, leave as-is
    "d1_t2e": ("load", 20, None),          # Better Fly Standing Lateral Raise -- new, real Wk1 locked
    "d1_t2c": ("assist", 25, None),        # unchanged
    "d1_t3a": ("assist", None, None),      # Wide-Grip Pull-up is PULL_UP_ROLLING_MAX -- no scalar baseline (unassisted, rolling-max tracked via unassisted_max_rolling, not current_load/assist_level). REMOVE any existing "d1_t3a" baseline entry rather than setting one here -- Wide-Grip Pull-up movements across the program (D4/D6) never get a BASELINES entry, this must match that pattern.
    "d1_t3c": ("load", 70, None),          # Lat Prayer -- real Wk1 locked, was different value before
    "d1_t3d": ("load", 0, None),           # Ab Wheel Rollout -- REP_LADDER/bodyweight, verify whether "load":0 or a different baseline shape fits REP_LADDER movements (check an existing REP_LADDER baseline entry, e.g. Ab Wheel's old d1_t4b entry before this rewrite, for the right value/None convention)
```

Resolve the `?`/verification notes above by reading the CURRENT `BASELINES` dict for `d1_t2d` and the old `d1_t4b` (Ab Wheel's previous slot_id) entries before writing this step's final diff — don't guess.

- [ ] **Step 8: Update `tests/test_golive_phase1.py`'s `EXPECTED_NEEDS_CAL`**

D1's slots should NOT appear as needs-cal (all seeded from real data). Remove any pre-existing D1 entries if present; do not add new ones for D1.

- [ ] **Step 9: Update `tests/test_library_seed.py` counts**

+1 movement (Better Fly Standing Lateral Raise), +1 ACTIVE. Update `test_total_count_103` and `test_status_counts` with incremented values and a dated comment, matching this file's established comment style.

- [ ] **Step 10: Fix structural tests**

`tests/test_generation_skeleton.py` and `tests/test_generation_assembler.py` likely have D1-specific assertions (tier composition, movement names) that need updating to match the new T2/T3 GS membership — grep both files for `"D1 Upper Push"` and any of the removed/added movement names (`Incline DB Press`, `Cross-Body Lateral Raise`, `Better Fly Standing Lateral Raise`, `Ab Wheel`) and update expected values to match Step 3's structure.

- [ ] **Step 11: Run the full suite**

```bash
cd ~/projects/IronLog-V2 && .venv/bin/python -m pytest -q
```
Expected: all passing, no regressions outside the files touched above.

- [ ] **Step 12: Commit**

```bash
git add ironlog/seed.py ironlog/generation/program_seed.py ironlog/generation/rule_wiring.py ironlog/generation/baseline_seed.py docs/program/phase1-seed-source.yaml tests/test_program_seed_yaml_parity.py tests/test_golive_phase1.py tests/test_library_seed.py tests/test_generation_skeleton.py tests/test_generation_assembler.py
git commit -m "feat(program): D1 reconciled to maintenance-block Wk1 reality (STAB redesign)"
```

- [ ] **Step 13: Apply to production and verify live**

SSH to myflix, run a Python script mirroring this session's established live-update pattern: create the `Better Fly Standing Lateral Raise [FT]` `Movement` row, repoint/relabel D1's `TierExercise` rows to match Step 3 exactly (rep ranges, movement_id for `d1_t3a`, new rows for `d1_t2e`/`d1_t3d`, removed rows for the old `d1_t2b`/`d1_t3b`), set `MovementState` values per Step 7's table, run `ironlog.generation.rule_wiring.main()`, restart `ironlogv2`, then run a direct `generate_session("D1 Upper Push", ...)` call confirming the assembled session matches the target table at the top of this task and that `Bench Press [PB]`/`Pendlay Row - Narrow [OB]`/`Lat Prayer` prescribe from their real seeded loads (not needs-calibration).

---

## Task 2: D2 — Lower Squat + new core tier

**Files:** same 5 seed/config files as Task 1, plus the same test files.

**Interfaces:**
- Consumes: existing `Belt Squat [GHR + FT]`, `ATG Split Squat`, `Cable Tibialis Raise` movements.
- Produces: new movements `Matrix Machine Sissy Squat`, `Nordic Curl Max [Ares]`, `Hybrid Board Calf Raise [D2]`, `Ab Trainer Decline Sit-up`. `Nordic Curl Max` is referenced again by Task 4 (D5) — same `Movement` row, shared identity, day-scoped `MovementState`.

### Target D2 structure

| Tier | Movement | Reps | Rule |
|---|---|---|---|
| T1 | Belt Squat [GHR + FT] | 4-6 | REP_LADDER (at 260 cap) |
| T2 GS | Matrix Machine Sissy Squat (new) | 8-12 | RPE_8_STANDARD |
| T2 GS | Nordic Curl Max [Ares] (new) | 6-8 | ASSISTANCE_REDUCTION, ladder `[60,55,50,45,40,35,30,25,20,15,10,5,0]` |
| T3 GS | ATG Split Squat | 8-12 | RPE_8_STANDARD (unchanged) |
| T3 GS | Hybrid Board Calf Raise [D2] (new) | 10-15 | RPE_8_STANDARD |
| T3 GS | Cable Tibialis Raise | 10-15 | RPE_8_STANDARD (unchanged) |
| T4 straight (new tier) | Ab Trainer Decline Sit-up (new) | 10-15 | REP_LADDER |

Removed: Hip Thrust (D2 T1b — the whole tier is dropped, not just the movement).

- [ ] **Step 1: Add 4 new movements to `ironlog/seed.py`**

```python
    dict(name="Matrix Machine Sissy Squat", base_name="Matrix Machine Sissy Squat",
         region=Region.LOWER, status=Status.ACTIVE, load_code=None, tags=["MATRIX"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         primary_muscle="QUADS", secondary_muscles=[]),

    dict(name="Nordic Curl Max [Ares]", base_name="Nordic Curl Max",
         region=Region.LOWER, status=Status.ACTIVE, load_code="FT", tags=["FT", "NORDIC_MAX"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         assist_ladder=[60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0],
         primary_muscle="HAMSTRINGS", secondary_muscles=["GLUTES"]),

    dict(name="Hybrid Board Calf Raise [D2]", base_name="Hybrid Board Calf Raise",
         region=Region.LOWER, status=Status.ACTIVE, load_code=None, tags=["HYBRID_BOARD"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         primary_muscle="CALVES", secondary_muscles=[]),

    dict(name="Ab Trainer Decline Sit-up", base_name="Ab Trainer Decline Sit-up",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["AB_TRAINER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         primary_muscle="ABS", secondary_muscles=[]),
```

Note: `Nordic Curl Max [Ares]` uses `REP_RATIO`/`ASSISTED` matching every other cable/band-assisted movement's shape (Wide-Grip Pull-up [TOWER+TUBES], Dips). `Ab Trainer Decline Sit-up` mirrors `Ab Wheel [WHEEL]`'s `PROTOCOL`/`STRAIGHT` shape (bodyweight, no scalar load, `REP_LADDER`-driven via `rep_ladder_at_cap` mapping).

- [ ] **Step 2: `PROGRAM_TO_LIBRARY` additions** (in the D2 section)

```python
    "Matrix Machine Sissy Squat":                   "Matrix Machine Sissy Squat",
    "Nordic Curl Max":                               "Nordic Curl Max [Ares]",
    "Hybrid Board Calf Raise D2":                   "Hybrid Board Calf Raise [D2]",
    "Ab Trainer Decline Sit-up":                    "Ab Trainer Decline Sit-up",
```

- [ ] **Step 3: Rewrite `_seed_d2`**

Read the current `_seed_d2` function in full first (it has D2's existing T1/T1b/T2/T3 structure with Belt Squat, the old Hip Thrust T1b, Lying Leg Curl, Reverse Hyper, ATG Split Squat, Cable Tibialis Raise, Reverse Nordic Curl). Rewrite it to: drop the T1b Hip Thrust tier entirely, change T1's rep range to 4-6, replace T2 GS's `Lying Leg Curl [GHR]` + `Reverse Hyper [REV_HYPER]` pairing with `Matrix Machine Sissy Squat` + `Nordic Curl Max [Ares]`, add `Hybrid Board Calf Raise [D2]` into T3 GS alongside the retained `ATG Split Squat` and `Cable Tibialis Raise` (drop `Reverse Nordic Curl [GHR]` from T3 — not in the FINAL source doc's D2), and add a new T4 straight tier (`TierKind.T1_STRAIGHT`, tier_order 5, rest 90s per the source doc's warmup/T4 pattern) with `Ab Trainer Decline Sit-up`. Use fresh `slot_id`s for every new movement (e.g. `d2_t2d`, `d2_t2e`, `d2_t3d`, `d2_t4a`) — do not reuse Hip Thrust's or Lying Leg Curl's old slot_ids per the never-reassign convention. Tier orders renumber sequentially (T1=1, T2 GS=2 [T1b gone], T3 GS=3, T4=4).

- [ ] **Step 4: yaml `d2:` block rewrite** — mirror Task 1 Step 4's pattern: drop the `T1b` block entirely, update `T1_STRAIGHT` reps to `[4,6]` with `current_load: 260`, rewrite `T2_GIANT`/`T3_GIANT` ex lists per Step 3's composition, add a new `T4_STRAIGHT` block for `ab_trainer_decline_situp_d2`.

- [ ] **Step 5: `rule_wiring.py` + its test-file copy** — add `matrix_machine_sissy_squat`, `nordic_curl_max_d2`, `hybrid_board_calf_raise_d2`, `ab_trainer_decline_situp_d2` to `YAML_M_TO_LIBRARY` in both files, pointing at the Step 1 movement names. `nordic_curl_max_d2` → `assistance_reduction` rule, others → `rpe_8_standard`/`rep_ladder_at_cap` per Step 4's yaml.

- [ ] **Step 6: `baseline_seed.py`** — Belt Squat stays `260` (unchanged, just rep-range context). New slots get NO baseline entries (needs-calibration is correct — all four are genuinely new movements with zero prior history, matching this session's established convention for brand-new movements).

- [ ] **Step 7: `test_golive_phase1.py`'s `EXPECTED_NEEDS_CAL`** — add `"D2 Lower A": {"Matrix Machine Sissy Squat", "Nordic Curl Max [Ares]", "Hybrid Board Calf Raise [D2]", "Ab Trainer Decline Sit-up"}` (merge with any pre-existing D2 entry rather than overwrite, e.g. `Lying Leg Curl [GHR]` needs-cal status — check whether Lying Leg Curl is still programmed anywhere; if D2 dropped it, its needs-cal entry should be removed too since it's no longer generated at all).

- [ ] **Step 8: `test_library_seed.py` counts** — +4 movements, +4 ACTIVE.

- [ ] **Step 9: Structural tests** — update `test_generation_skeleton.py`/`test_generation_assembler.py` D2 assertions to match the new tier composition (T1b tier removed entirely — any test asserting D2 has a T1b tier needs updating).

- [ ] **Step 10: Run full suite, commit** (same pattern as Task 1 Steps 11-12).

- [ ] **Step 11: Apply to production** — create the 4 new `Movement` rows, rebuild D2's `Tier`/`TierExercise` rows to match (drop T1b entirely — delete its `TierExercise` and `Tier` rows, mirroring how prior sessions have restructured tiers), set Belt Squat's rep range, run `rule_wiring.main()`, restart, verify live via `generate_session("D2 Lower A", ...)` confirming Hip Thrust no longer appears and all 4 new movements do.

---

## Task 3: D4 — Upper Pull + Vertical Press

**Files:** same pattern as Tasks 1-2.

**Interfaces:**
- Consumes: existing `Rear Delt Fly [DB]`, `Lying Tricep Extension [SB]`... wait, D4 needs its OWN camber-7" tricep extension movement per the source doc (D4 uses camber_7 grip) — actually per source doc D4's T3 has `lying_tricep_extension_camber_7`, and D1's T2 already has a `Lying Tricep Extension [SB]` at an unspecified grip. Check whether these are meant to be the SAME movement (shared) or distinct grip-specific movements (mirrors the camber-bar-grip-variant precedent from the design doc §3/revision-1's original camber discussion). Resolve by checking whether the existing `Lying Tricep Extension [SB]` movement has a grip already implied in its name/notes — if ambiguous, treat as the SAME shared movement (simpler, avoids an unnecessary near-duplicate) unless a later task surfaces a reason they must diverge.
- Produces: `Seated BTN OHP`, `Better Fly Lat Pulldown`, `Stryker Pad CSR Barbell`, `Better Fly Cable Pullover`, `Cable Woodchopper` — check `PureTorque Pro Rotation` (already exists, built earlier this session as D4's Cable Woodchopper equivalent per an earlier task) before creating a new "Cable Woodchopper" movement; the source doc's `cable_woodchopper` entry likely maps to the ALREADY-EXISTING `PureTorque Pro Rotation [...]` movement (same equipment: `ares_high_pulley`/`puretorque_pro`) rather than a new one — confirm via `grep -n "PureTorque Pro Rotation" ironlog/seed.py` before writing this task's movement-creation step, and if it matches, reuse it (no new movement, just rewire D4's T3 to point at it, matching the source doc's `equipment: [ares_high_pulley, puretorque_pro]` which is IDENTICAL to how PureTorque Pro Rotation was originally speced).

### Target D4 structure

| Tier | Movement | Reps | Rule |
|---|---|---|---|
| T1 | Seated BTN OHP (new) | 4-6 | RPE_8_STANDARD |
| T1b | Better Fly Lat Pulldown (new, replaces Wide-Grip Pull-up) | 6-8 | RPE_8_STANDARD |
| T2 GS | Stryker Pad CSR Barbell (new) | 8-12 | RPE_8_STANDARD |
| T2 GS | Ab Trainer Hanging Leg Raise (existing? check — likely already exists from an earlier session task, reuse if so) | 8-12 | REP_LADDER |
| T2 GS | Better Fly Cable Pullover (new) | 10-15 | RPE_8_STANDARD |
| T3 GS | DB Rear Delt Fly / Rear Delt Fly [DB] (existing, reuse) | 10-15 | RPE_8_STANDARD |
| T3 GS | Lying Tricep Extension [SB] (existing, reuse — see Interfaces note) | 8-12 | RPE_8_STANDARD |
| T3 GS | PureTorque Pro Rotation (existing, reuse — see Interfaces note) | 8-12 | RPE_8_STANDARD |

- [ ] **Step 1: Verify reuse candidates before creating anything**

```bash
cd ~/projects/IronLog-V2 && grep -n "PureTorque Pro Rotation\|Ab Trainer Hanging Leg Raise\|Rear Delt Fly \[DB\]" ironlog/seed.py
```

Confirm each already exists with a name/shape matching the target table; if `Ab Trainer Hanging Leg Raise` does NOT already exist, add it in Step 2 alongside the genuinely-new movements (it's plausible it doesn't yet, since D2's `Ab Trainer Decline Sit-up` from Task 2 was new — Ab Trainer apparatus is new equipment this block).

- [ ] **Step 2: Add new movements to `ironlog/seed.py`**

```python
    dict(name="Seated BTN OHP [PB]", base_name="Seated BTN OHP",
         region=Region.UPPER, status=Status.ACTIVE, load_code="PB", tags=["PB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         primary_muscle="FRONT_DELT", secondary_muscles=["TRICEPS", "SIDE_DELT"]),

    dict(name="Better Fly Lat Pulldown [FT]", base_name="Better Fly Lat Pulldown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT", "BETTER_FLY"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10,
         primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),

    dict(name="Stryker Pad CSR Barbell [OB]", base_name="Stryker Pad CSR Barbell",
         region=Region.UPPER, status=Status.ACTIVE, load_code="OB", tags=["OB", "STRYKER_PAD"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),

    dict(name="Better Fly Cable Pullover [FT]", base_name="Better Fly Cable Pullover",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT", "BETTER_FLY"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10,
         primary_muscle="LATS", secondary_muscles=["MID_BACK"]),
```

If Step 1 found `Ab Trainer Hanging Leg Raise` missing, add:
```python
    dict(name="Ab Trainer Hanging Leg Raise", base_name="Ab Trainer Hanging Leg Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["AB_TRAINER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         primary_muscle="ABS", secondary_muscles=["HIP_FLEXOR"]),
```

- [ ] **Step 3: `PROGRAM_TO_LIBRARY` additions**, **Step 4: rewrite `_seed_d4`** (drop the T1b Wide-Grip Pull-up `_add_te` call, add Better Fly Lat Pulldown in its place at the SAME slot_id per the never-reassign rule ONLY IF the design doc treats this as "the same conceptual slot changing content" — re-check design doc §5: D4 "loses pull-ups, gains Better Fly Lat Pulldown" reads as a genuine content swap of the T1b anchor slot, same treatment as D1's `d1_t3a` in Task 1 — reuse `d4_t1`'s existing slot_id), rewire T2/T3 per the target table with fresh slot_ids for the genuinely new T2 members, **Step 5: yaml rewrite**, **Step 6: rule_wiring + test copy**, **Step 7: baseline_seed** (new movements = no baseline, needs-cal), **Step 8: EXPECTED_NEEDS_CAL**, **Step 9: library counts**, **Step 10: structural tests**, **Step 11: full suite + commit**, **Step 12: apply to production + verify live** — same shapes as Task 2's Steps 3-11, applied to D4's specifics.

---

## Task 4: D5 — Lower Hinge + new core tier

**Files:** same pattern.

**Interfaces:**
- Consumes: `Nordic Curl Max [Ares]` (Task 2, shared movement, day-scoped state), `Reverse Nordic Curl [GHR]` (existing).
- Produces: `Kickstand RDL [DB]`, `Nordic Max Bulgarian Split Squat`, `Better Fly Kickback`, `Hybrid Board Calf Raise [D5]` (separate movement from D2's per the source doc's "independent_track" framing — unlike Nordic Curl Max which is explicitly ONE shared movement with day-scoped state, the calf raise entries are named with day suffixes AND the source doc says "independent track from d2_hybrid_board_calf" — this reads as two genuinely separate `Movement` rows, not one shared row; resolve by checking whether "independent track" in this doc consistently means shared-movement-with-day-scoped-state (Nordic Curl Max's own note says "independent_track: from_d2_nordic_curl_max" using identical language) — if the language is identical for both, treat BOTH the same way (shared movement, day-scoped MovementState), for consistency, rather than picking two different representations for the same stated concept), `Better Fly Hip Adduction`, `Ab Trainer Russian Twist`.

### Target D5 structure

| Tier | Movement | Reps | Rule |
|---|---|---|---|
| T1 | Kickstand RDL [DB] (new, replaces RDL [PB]) | 4-6 | RPE_8_STANDARD |
| T2 GS | Nordic Max Bulgarian Split Squat (new) | 8-12 | RPE_8_STANDARD |
| T2 GS | Nordic Curl Max [Ares] (shared with D2, Task 2) | 6-8 | ASSISTANCE_REDUCTION |
| T2 GS | Better Fly Kickback (new) | 10-15 | RPE_8_STANDARD |
| T3 GS | Reverse Nordic Curl [GHR] (existing, reuse) | 8-12 | ASSISTANCE_REDUCTION (unchanged) |
| T3 GS | Hybrid Board Calf Raise (shared with D2 per the "independent track" resolution above, OR separate — resolve per the Interfaces note before writing code) | 10-15 | RPE_8_STANDARD |
| T3 GS | Better Fly Hip Adduction (new) | 10-15 | RPE_8_STANDARD |
| T4 straight (new tier) | Ab Trainer Russian Twist (new) | 10-15 | REP_LADDER |

Removed: Hip Thrust (D5 T1b — whole tier dropped), `RDL [PB]` drops out of D5's rotation (Kickstand RDL replaces it; `RDL [PB]` itself is untouched in the library since D5's own meso-2 rotation logic and any other reference elsewhere may still need it — do not retire the movement itself, only stop wiring it into D5).

- [ ] **Step 1: Add new movements** (Kickstand RDL, Nordic Max BSS, Better Fly Kickback, Better Fly Hip Adduction, Ab Trainer Russian Twist — plus Hybrid Board Calf Raise ONLY if the Interfaces-note resolution concludes it needs a separate D5 row), mirroring Task 2/3's dict shapes: `Kickstand RDL [DB]` unilateral (`unilateral=True`), `progression_mode=LADDER`, `increment_ladder=[2.5]`, `load_floor=10` (matches other unilateral DB movements like `Single-Arm DB Row [DB]`); the rest follow the same `RPE_8_STANDARD`/`DOUBLE_PROGRESSION` shape as prior tasks' new movements.

- [ ] **Steps 2-11**: same pattern as Task 2 (PROGRAM_TO_LIBRARY, `_seed_d5` rewrite dropping T1b entirely, adding the new T4 core tier, yaml, rule_wiring + test copy, baseline_seed, EXPECTED_NEEDS_CAL, library counts, structural tests, full suite + commit, apply to production + verify live).

---

## Task 5: D6 — Weak Points + Isolation

**Files:** same pattern.

**Interfaces:**
- Consumes: `Dips [TOWER + TUBES]` (existing, unchanged), `Face Pull [FT]` (existing, unchanged), `Wide-Grip Pull-up [TOWER + TUBES]` (already exists — built earlier this session for D6's assisted slot, per design doc §5, NO change needed here beyond confirming rep range still matches 5-8).
- Produces: `Close-Grip Bench Camber-14`, `Better Fly Cable Bicep Curl`, `Stryker Pad CSR Cables`, `Better Fly Rear Delt Extension`, `Better Fly OH Tricep Extension`, `AbMat Ab Bench Pad Cable Crunch`.

### Target D6 structure

| Tier | Movement | Reps | Rule |
|---|---|---|---|
| GS1 | Wide-Grip Pull-up [TOWER + TUBES] (existing, unchanged) | 5-8 | ASSISTANCE_REDUCTION |
| GS1 | Dips [TOWER + TUBES] (existing, unchanged) | 8-12 | RPE_8_STANDARD |
| GS1 | Close-Grip Bench Camber-14 (new) | 4-6 | RPE_8_STANDARD |
| GS2 | Better Fly Cable Bicep Curl (new) | 10-15 | RPE_8_STANDARD |
| GS2 | Stryker Pad CSR Cables (new) | 8-12 | RPE_8_STANDARD |
| GS2 | Better Fly Rear Delt Extension (new) | 10-15 | RPE_8_STANDARD |
| GS3 | Face Pull [FT] (existing, unchanged) | 10-15 | RPE_8_STANDARD |
| GS3 | Better Fly OH Tricep Extension (new) | 8-12 | RPE_8_STANDARD |
| GS3 | AbMat Ab Bench Pad Cable Crunch (new) | 10-15 | RPE_8_STANDARD |

Removed: `Hip Thrust [HIP_THRUST]` (D6's derived-from-unified GS1 slot — this one needs extra care, see below), `Cable Bicep Curl [FT]` (D6's existing bicep slot, replaced by Better Fly Cable Bicep Curl), `T-Bar Row - Wide [OB + KLEVA + LM]` and `Cable V-Bar Pushdown [FT]` (GS2's current members, replaced).

**Special handling — D6's Hip Thrust removal**: D6's Hip Thrust `TierExercise` had `derived_from_unified_group="main"`/`derive_ratio=0.8` (spec 52 — it live-derives from D2/D5's unified Hip Thrust progression). Since D2 and D5 are ALSO dropping Hip Thrust in this same redesign (Tasks 2 and 4), the entire `HtProgressionState` "main" group becomes fully orphaned once all three days drop it — leave the `HtProgressionState` row in place (harmless, matches convention), but double check `ironlog/generation/loop.py`'s `commit_session` derive-push loop doesn't error when NO `TierExercise` anywhere references `derived_from_unified_group="main"` anymore (it shouldn't — the loop only fires for movements matching that filter, and an empty result set is a no-op, but confirm by reading the loop before Task 2 removes D2's Hip Thrust, since that's the first of the three removals).

- [ ] **Step 1-11**: same pattern as prior tasks. New movements: `Close-Grip Bench Camber-14` (reuse the "BMF Camber Bar" equipment/`SB` load_code, 3rd grip variant alongside D1's 21" and D4's 7" — mirrors the design doc's §3 camber-grip-variant precedent), the rest follow established `RPE_8_STANDARD`/`DOUBLE_PROGRESSION` `Better Fly`/`Stryker Pad`/`AbMat` shapes from Tasks 1-4.

---

## Task 6: Phase flip CUT → STAB

**Files:** none (data-only, no code change — this is a live-DB-only task).

- [ ] **Step 1: Verify all 5 days generate cleanly on production**

```bash
ssh myflix "cd ~/projects/IronLog-V2 && .venv/bin/python -c \"
from sqlmodel import Session, select
from ironlog.db import engine
from ironlog.api.app import _make_proposer, _week_keyer
from ironlog.generation.loop import generate_session
from ironlog.generation.skeleton import lay_skeleton

with Session(engine) as db:
    for role in ['D1 Upper Push', 'D2 Lower A', 'D4 Upper Pull', 'D5 Lower B', 'D6 Weak Points']:
        sk = lay_skeleton(role, db)
        proposer = _make_proposer(sk)
        outcome = generate_session(role, db, proposer, _week_keyer)
        assert outcome.assembled is not None, (role, outcome.rejections)
        print(role, 'OK')
\""
```

- [ ] **Step 2: Flip the phase**

```bash
ssh myflix "cd ~/projects/IronLog-V2 && .venv/bin/python -c \"
from sqlmodel import Session, select
from ironlog.db import engine
from ironlog.models.library import EngineState
from ironlog.models.enums import Phase

with Session(engine) as db:
    es = db.exec(select(EngineState)).one()
    print('before:', es.current_phase)
    es.current_phase = Phase.STAB
    db.add(es)
    db.commit()
    db.refresh(es)
    print('after:', es.current_phase)
\""
```

- [ ] **Step 3: Restart and verify RPE band / backoff count changed**

```bash
ssh myflix "sudo systemctl restart ironlogv2 && sleep 2 && curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/docs"
```

Confirm via a fresh `generate_session` call for any T1 primary that the phase-policy-driven fields (RPE cap, back-off set count) reflect `Phase.STAB`'s policy row rather than `Phase.CUT`'s.

---

## Task 7: Final full-week verification + completion report

- [ ] **Step 1**: Full test suite green on the final state.
- [ ] **Step 2**: `generate_session` for all 5 training days + confirm both rest days (D3, D7) have no training content (unchanged, `is_rest=True`).
- [ ] **Step 3**: Spot-check D1's real baselines produce sane Wk2 prescriptions (not needs-calibration) for Bench Press, Pendlay Row (held at 170), Lat Prayer, Stryker Pad Seated OHP.
- [ ] **Step 4**: Write the completion report to `~/project-ops/reports/` per this session's established template (Objective/What Changed/Delegation & Review/Build Verification/Deploy/Issues & Decisions/Rollback/Open Items/Hand-off), covering all 5 days + phase flip as one batch, noting the design doc's Open Items section verbatim as the report's own open items.
- [ ] **Step 5**: Commit the report.

---

## Self-Review Notes (from plan authoring)

- **Spec coverage**: all 7 design-doc sections (phase transition, rep-range drop, core-every-session, equipment translation, pull-up architecture, Nordic Curl Max, D1 baselines, Pendlay hold, Belt Squat deferral, retirement list) map to a task above.
- **Open resolution points intentionally left for the implementer** (not placeholders — genuine "verify before writing" instructions, consistent with this repo's established discipline of checking current state rather than guessing): Task 1 Step 7's exact `d1_t2d`/`d1_t3d` baseline values, Task 3's PureTorque Pro Rotation / Ab Trainer Hanging Leg Raise reuse check, Task 4's Hybrid Board Calf Raise shared-vs-separate movement resolution. Each names the exact command to run and the exact decision rule to apply — resolve them by reading current state, not by inventing a value.
- **Type/name consistency**: `Nordic Curl Max [Ares]` name is used identically across Tasks 2 and 4; `PureTorque Pro Rotation` and `Ab Trainer Hanging Leg Raise` reuse checks in Task 3 use the same movement-name strings a later grep would find.
