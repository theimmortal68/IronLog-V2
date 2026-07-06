# Config-Seed Engine Go-Live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile the entire Phase-1 program seed (`program_seed.py`) to the authoritative `docs/program/phase1-seed-source.yaml`, seed every D1–D6 MovementState baseline (loads + HT band-composite) day-scoped, reset test data, and verify all five days generate cleanly — so the progression engine goes live fully calibrated for Week 1.

**Architecture:** The YAML at `docs/program/phase1-seed-source.yaml` is the source of truth; `program_seed._seed_dN` had drifted from it (stale interim structure). This chunk makes the seeder match the YAML, adds a YAML-parity test to prevent re-drift, seeds `MovementState` baselines keyed on `(movement_id, day_id=day_role)`, and fixes `context.py` to load states day-scoped (the v0.6 composite-key design) so per-day HT/accessory tracks don't collide. A go-live script ties reseed + baselines + reset together with a `--verify` pass.

**Tech Stack:** Python 3.14, FastAPI, SQLModel, SQLite, pytest. Server tests run on the myflix server: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.

## Global Constraints

- NO `from __future__ import annotations` in any server file (project-wide).
- Migrations are **additive only** (`ADD COLUMN` / `CREATE TABLE`) — the additive-schema carve-out per `deploy/migrations/README.md`. The parity keystone `tests/test_migrations.py::test_chain_matches_create_all` must stay green (the migration chain DDL must exactly match SQLModel `create_all`).
- **Option-C two-writer boundary:** `commit_session` remains the sole writer of `current_load`/`ht_plates`/`ht_band_config` during *live use*. Baseline **seeding** (this chunk, pre-launch) sets those directly — that is the calibrated go-live baseline, not a live-use write. `run_analysis` still writes no MovementState during generation.
- `engine/` stays pure (no DB writes in the pure engine).
- The seeder's `_resolve()` halt-and-flag guard is sacred: never invent or silently skip a movement; a missing movement must `ValueError`.
- Baseline number of server tests before this chunk: ~433 (from the note-apply-redesign merge). All must stay green; net-new tests add to that.
- **This reseed is destructive and pre-launch-only.** Once real Week-1 logging starts it is NOT re-runnable (backup/pull-before-push discipline resumes). The destructive live run is a gated manual step (see "Go-Live Execution"), NOT an automated task.
- Authoritative per-slot values live in `docs/program/phase1-seed-source.yaml` and the baseline table in Task 4 — use those exact numbers.

**Decisions locked (2026-07-06):** Face Pull → new `Face Pull [FT]`. Cross-Body Rear Delt Fly dropped from D6. Seed all D1–D6 baselines directly (fully calibrated, no wizard). Keep existing Meso-2 rotations (Belt→Back Squat, Meadows→Pendlay) + annotate them in the YAML. Flip Lat Prayer + Dips to ACTIVE. Build the D5 single-leg Scout Reverse Hyper Meso-2 now (new movement + MesoRotation rep-override).

---

### Task 1: Library prep — new movements + ACTIVE flips

**Files:**
- Modify: `ironlog/seed.py` (the `MOVEMENTS` list)
- Test: `tests/test_library_seed.py`

**Interfaces:**
- Produces: three library movements resolvable by exact name — `Face Pull [FT]`, `Reverse Hyper - Single Leg [REV_HYPER]`, and `Dips [ANDREONI + FT]`/`Lat Prayer [ANDREONI + FT]` now `Status.ACTIVE`. Task 3 maps program names to these.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_library_seed.py`:

```python
def test_golive_library_additions(gen_db):
    from ironlog.models.library import Movement
    from ironlog.models.enums import Status
    from sqlmodel import select
    by_name = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    # new movements
    assert "Face Pull [FT]" in by_name
    assert "Reverse Hyper - Single Leg [REV_HYPER]" in by_name
    slscout = by_name["Reverse Hyper - Single Leg [REV_HYPER]"]
    assert slscout.unilateral is True
    assert slscout.load_code == "REV_HYPER"
    # ACTIVE flips (live-programmed movements)
    assert by_name["Lat Prayer [ANDREONI + FT]"].status == Status.ACTIVE
    assert by_name["Dips [ANDREONI + FT]"].status == Status.ACTIVE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_seed.py::test_golive_library_additions -q'`
Expected: FAIL (KeyError / assert False — movements absent, statuses INACTIVE)

- [ ] **Step 3: Add the two new movements to `MOVEMENTS`**

Add `Face Pull [FT]` near the existing `Face Pull w/ ER Hold [FT]` (a loaded FT cable rear-delt movement, mirroring the DOUBLE_PROGRESSION FT pattern):

```python
    dict(name="Face Pull [FT]", base_name="Face Pull",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=2.5, load_floor=10,
         primary_muscle="REAR_DELT", secondary_muscles=["UPPER_TRAPS"]),
```

Add the single-leg Scout next to `Reverse Hyper [REV_HYPER]` (mirror its dict, unilateral, not the family anchor):

```python
    dict(name="Reverse Hyper - Single Leg [REV_HYPER]", base_name="Reverse Hyper - Single Leg",
         region=Region.LOWER, lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"], unilateral=True,
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         load_floor=0, family="reverse_hyper",
         primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
```

- [ ] **Step 4: Flip Lat Prayer + Dips to ACTIVE**

In the `Lat Prayer [ANDREONI + FT]` dict (~line 616) and the `Dips [ANDREONI + FT]` dict (~line 583), change `status=Status.INACTIVE` → `status=Status.ACTIVE`.

- [ ] **Step 5: Run the test + the full library-seed suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_seed.py -q'`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ironlog/seed.py tests/test_library_seed.py
git commit -m "feat(seed): add Face Pull [FT] + single-leg Scout Reverse Hyper; ACTIVE-flip Lat Prayer + Dips"
```

---

### Task 2: MesoRotation rep-range override + migration 023

**Files:**
- Modify: `ironlog/models/program.py` (`MesoRotation`)
- Create: `deploy/migrations/023_mesorotation_reps.sql`
- Test: `tests/test_migrations.py` (parity keystone already asserts this), `tests/test_program_seed_rotation_guard.py`

**Interfaces:**
- Produces: `MesoRotation.rep_low: Optional[int]` and `MesoRotation.rep_high: Optional[int]` (nullable; None = inherit the base TierExercise reps). Task 3 sets them for the D5 single-leg Scout Meso-2 row.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_program_seed_rotation_guard.py`:

```python
def test_mesorotation_has_rep_override_fields(gen_db):
    from ironlog.models.program import MesoRotation
    cols = MesoRotation.__table__.columns.keys()
    assert "rep_low" in cols and "rep_high" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_program_seed_rotation_guard.py::test_mesorotation_has_rep_override_fields -q'`
Expected: FAIL (columns absent)

- [ ] **Step 3: Add the columns to the model**

In `ironlog/models/program.py`, in `class MesoRotation`, after `movement_id`:

```python
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
```

(`Optional` is already imported in this module; confirm.)

- [ ] **Step 4: Write migration 023**

Create `deploy/migrations/023_mesorotation_reps.sql`. Derive the exact DDL to match SQLModel by running, from the repo root:

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -c "
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite
from ironlog.models.program import MesoRotation
print(CreateTable(MesoRotation.__table__).compile(dialect=sqlite.dialect()))"'
```

Write the two additive statements (nullable INTEGER, no default), matching the SQLite column type the parity test expects:

```sql
ALTER TABLE mesorotation ADD COLUMN rep_low INTEGER;
ALTER TABLE mesorotation ADD COLUMN rep_high INTEGER;
```

- [ ] **Step 5: Run the parity keystone + rotation-guard tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_migrations.py tests/test_program_seed_rotation_guard.py -q'`
Expected: PASS (`test_chain_matches_create_all` green — chain DDL == create_all)

- [ ] **Step 6: Commit**

```bash
git add ironlog/models/program.py deploy/migrations/023_mesorotation_reps.sql tests/test_program_seed_rotation_guard.py
git commit -m "feat(model): MesoRotation rep_low/rep_high override + migration 023 (additive)"
```

---

### Task 3: Reconcile program_seed to the authoritative YAML (all days)

**Files:**
- Modify: `ironlog/generation/program_seed.py` (`PROGRAM_TO_LIBRARY`, `_seed_d1`, `_seed_d2`, `_seed_d4`, `_seed_d5`, `_seed_d6`)
- Modify: `docs/program/phase1-seed-source.yaml` (annotate the two existing Meso-2 rotations)
- Create: `tests/test_program_seed_yaml_parity.py` (the anti-drift keystone)
- Test: `tests/test_program_seed.py`, `tests/test_phase1_reconciliation.py` must stay green

**Interfaces:**
- Consumes: library movements + mappings from Task 1; `MesoRotation.rep_low/rep_high` from Task 2.
- Produces: a seeded program whose per-day tier/slot structure matches the YAML. Task 4 keys baselines on the resulting slot_ids (unchanged slot_id scheme: `d1_t1`, `d2_t1b`, `d4_t2b`, `d6_g2a`, etc.).

**Add these `PROGRAM_TO_LIBRARY` entries** (in the D4/D5/D6 sections):

```python
    "DB Rear Delt Fly":                             "Rear Delt Fly [DB]",
    "Reverse Nordic (assisted)":                    "Reverse Nordic Curl [GHR]",
    "Face Pull":                                    "Face Pull [FT]",
    "Scout Reverse Hyper - Single Leg":             "Reverse Hyper - Single Leg [REV_HYPER]",
```

**Per-day edits** (exact — from the verified YAML↔code diff; slot_ids in `_seed_dN` keep their names):

- [ ] **Step 1: D1 — `_seed_d1`**
  - `d1_t1` bench: `rep_low=8` → `rep_low=6`.
  - Tier `T3 GS` `_add_tier(...)`: `rest_seconds=60` → `rest_seconds=75`.
  - `d1_t3a` pull-up: `rep_low=8, rep_high=8` → `rep_low=6, rep_high=10`.
  - **Swap slot-3 movements:** `d1_t3c` must seed `"Lat Prayer"` (pattern `"lat"`, reps 12,12, scheme DOUBLE_PROGRESSION); `d1_t4c` must seed `"Cross-Body Rear Delt Fly"` (pattern `"rear_delt"`, reps 12,12, scheme DOUBLE_PROGRESSION). (They are currently reversed.)

- [ ] **Step 2: D2 — `_seed_d2`**
  - `d2_t1` belt squat: `rep_low=5` → `rep_low=6`.
  - Tier `T1b` `_add_tier`: `rest_seconds=120` → `150`.
  - `d2_t1b` HT: `tier_role="semi"` → `tier_role="anchor"`.
  - `d2_t2a` nordic: `rep_low=6, rep_high=10` → `rep_low=8, rep_high=8`.
  - `d2_t2b` scout: `rep_high=25` → `rep_high=15`.
  - Tier `T3` `_add_tier`: `rest_seconds=60, rounds=1` → `rest_seconds=75, rounds=3`. (Keep `TierKind.PAIR`? YAML `T3_PAIR` → keep PAIR; only rest/rounds change.)

- [ ] **Step 3: D4 — `_seed_d4` (restructure)**
  - Tier `T1` `_add_tier`: `rest_seconds=120` → `180`. `d4_t1` pull-up `rep_low=5` → `rep_low=6`.
  - T2 GS slots become: slot1 `d4_t2a` Meadows Row (`rep_low=8`→`rep_low=10`, keep meso-2 `_add_mr(..., "Pendlay Row")`); slot2 **NEW** `d4_t2b` = `"Single-Arm DB Row"` standalone (`_add_te(..., order=2, rep_low=12, rep_high=12, pattern="horizontal_pull", scheme="DOUBLE_PROGRESSION")`); slot3 `d4_t2c` Face-Up Incline Knee Raise (`rep_low=8`→`rep_low=12`).
  - **Remove** the old `d4_t3b` "Meadows SA Row" `_add_te` **and** its `_add_mr(..., "Single-Arm DB Row")` (that movement is now the standalone T2 slot2 above).
  - T3 GS `_add_tier`: `rest_seconds=60` → `75`. Slots become: slot1 `d4_t3a` = `"DB Rear Delt Fly"` (replaces "Cross-Body Rear Delt Fly"; pattern `"rear_delt"`, reps 12,12, drop unilateral, scheme DOUBLE_PROGRESSION); slot2 `d4_t3b` = Andreoni Cable Pullover moved here (`_add_te(..., order=2, "Andreoni Cable Pullover", rep_low=12, rep_high=12, pattern="lat", scheme="DOUBLE_PROGRESSION")`); slot3 `d4_t3c` Dragon Flag unchanged (reps 3,6).

- [ ] **Step 4: D5 — `_seed_d5`**
  - Tier `T1` `_add_tier`: `120`→`180`. `d5_t1` RDL reps `(4,6)`→`(6,8)`. (Meso-2 Staggered RDL unchanged.)
  - Tier `T1b` `_add_tier`: `120`→`150`. `d5_t1b` HT `tier_role="semi"`→`"anchor"`.
  - `d5_t2b` scout (bilateral, meso-1): `rep_low=12, rep_high=15` → `rep_low=15, rep_high=15`. **Add** its Meso-2 rotation to the single-leg movement with rep override:
    ```python
    _add_mr(db, d5_t2b, 2, "Scout Reverse Hyper - Single Leg", lib)  # returns MesoRotation mr
    # set rep override on the returned mr (see _add_mr change below)
    ```
    Update `_add_mr` to accept optional `rep_low`/`rep_high` and set them on the row:
    ```python
    def _add_mr(db, te, meso_number, prog_name, lib, rep_low=None, rep_high=None):
        mr = MesoRotation(tier_exercise_id=te.id, meso_number=meso_number,
                          movement_id=_resolve(prog_name, lib),
                          rep_low=rep_low, rep_high=rep_high)
        db.add(mr)
        return mr
    ```
    Then call: `_add_mr(db, d5_t2b, 2, "Scout Reverse Hyper - Single Leg", lib, rep_low=12, rep_high=15)`. Capture `d5_t2b` as the return of its `_add_te`. Replace the stale "intentionally NO MesoRotation" comment with a note that meso-2 is the single-leg movement with a 12–15 rep override.
  - `d5_t2c` nordic: `rep_low=5, rep_high=8` → `rep_low=8, rep_high=10`.
  - `d5_t3b`: `"Sissy Squat"` (knee=SISSY, reps 8,12) → `"Reverse Nordic (assisted)"`, `knee_modality=KneeModality.KOT`, `rep_low=8, rep_high=10`, `scheme="ASSISTED"`.
  - `d5_t3d` calf: `rep_high=12` → `rep_high=15`.

- [ ] **Step 5: D6 — `_seed_d6` (restructure)**
  - `d6_g1b` dips: `rep_low=5, rep_high=8` → `rep_low=8, rep_high=12`.
  - GS2 slots become: slot1 `d6_g2a` = `"Reverse Hyper Recovery"` (moved from GS3; pattern `"reverse_hyper"`, `rep_low=15, rep_high=20`, `rpe_cap=6.0`, `tier_role="free"`, scheme `"FIXED"`); slot2 `d6_g2b` DB Seal Row unchanged (10,12); slot3 `d6_g2c` Lateral Raise `rep_low=12`→`rep_low=10` (keep `rep_high=15`).
  - GS3 slots become: slot1 `d6_g3a` = `"Face Pull"` (`rep_low=12, rep_high=15`, pattern `"rear_delt"`, scheme `"DOUBLE_PROGRESSION"`, `tier_role="free"`) — **replaces** the dropped Cross-Body Rear Delt Fly; slot2 `d6_g3b` Cable V-Bar Pushdown unchanged (8,12, SINGLE_SESSION); slot3 `d6_g3c` = `"T-Bar Row Wide"` (moved from GS2; pattern `"horizontal_pull"`, `rep_low=8, rep_high=10`, `tier_role="semi"`, scheme `"DOUBLE_PROGRESSION"`).

- [ ] **Step 6: Annotate the two existing Meso-2 rotations in the YAML (documentation)**
  - `d2` T1 `belt_squat`: add `meso: {2: back_squat}`.
  - `d4` T2 `meadows_row_bruno_bar`: add `meso: {2: pendlay_row}`.

- [ ] **Step 7: Write the YAML-parity keystone test**

Create `tests/test_program_seed_yaml_parity.py` — parse the YAML and assert the seeded program matches it on the fields the model stores (per training day: tier labels/order, rest, rounds, shoe; per slot: resolved movement name, exercise_order, rep_low/rep_high). Skip YAML-only fields (load/assist/ht_*/unilateral/pattern) — those are baseline/Movement-layer.

```python
import yaml
from pathlib import Path
from sqlmodel import select
from ironlog.models.program import Program, ProgramDay, Tier, TierExercise
from ironlog.models.library import Movement
from ironlog.generation.program_seed import PROGRAM_TO_LIBRARY

DAY_MAP = {"d1": "D1 Upper Push", "d2": "D2 Lower A", "d4": "D4 Upper Pull",
           "d5": "D5 Lower B", "d6": "D6 Weak Points"}

def _yaml():
    p = Path(__file__).resolve().parents[1] / "docs/program/phase1-seed-source.yaml"
    return yaml.safe_load(p.read_text())["days"]

def test_seeded_reps_and_movements_match_yaml(gen_db):
    days = _yaml()
    mv = {m.id: m.name for m in gen_db.exec(select(Movement)).all()}
    # Build seeded {day_role: [ (movement_name, rep_low, rep_high) ... in tier/exercise order ]}
    for ykey, role in DAY_MAP.items():
        pd = gen_db.exec(select(ProgramDay).where(ProgramDay.day_role == role)).one()
        tiers = sorted(gen_db.exec(select(Tier).where(Tier.program_day_id == pd.id)).all(),
                       key=lambda t: t.tier_order)
        seeded = []
        for t in tiers:
            tes = sorted(gen_db.exec(select(TierExercise).where(TierExercise.tier_id == t.id)).all(),
                         key=lambda te: te.exercise_order)
            for te in tes:
                seeded.append((mv[te.movement_id], te.rep_low, te.rep_high))
        # Flatten YAML day
        expected = []
        for tier in days[ykey].values():
            for ex in tier["ex"]:
                canon = PROGRAM_TO_LIBRARY.get(_prog_name_for(ex["m"]), None)
                # _prog_name_for maps YAML m-id -> the program name string used in _seed;
                # simplest: assert by resolved library NAME + reps
                expected.append((canon, ex["reps"][0], ex["reps"][1]))
        # Compare movement names + reps positionally
        assert [s[0] for s in seeded] == [e[0] for e in expected], f"{role} movement order"
        assert [(s[1], s[2]) for s in seeded] == [(e[1], e[2]) for e in expected], f"{role} reps"
```

Note to implementer: the YAML `m:` ids (e.g. `pull_up_d6`) are not the `_seed` program-name strings. Build a small `YAML_M_TO_LIBRARY` dict in the test (YAML m-id → canonical library name) rather than reusing `PROGRAM_TO_LIBRARY` directly, OR extend the test to resolve via a shared table. Keep the test authoritative on **movement identity + reps + order per day**; that is the anti-drift guarantee. If a positional compare is brittle for meso/rest, assert per-(day, tier, slot). Make it pass by construction after Steps 1–6.

- [ ] **Step 8: Run parity + existing seed tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_program_seed.py tests/test_program_seed_yaml_parity.py tests/test_program_seed_rotation_guard.py tests/test_phase1_reconciliation.py -q'`
Expected: PASS. If `test_phase1_reconciliation.py` asserts stale D6 slot targets (e.g. `d6_g3c` rpe_cap), update `scripts/reconcile_phase1.py`'s `REP_TARGETS`/`RPE_CAPS` to the new slot layout (d6_g1b→(8,12); reverse-hyper rpe_cap now on `d6_g2a`) and regenerate `deploy/migrations/013` per that script's own regeneration step — keep it consistent, since the full reseed supersedes it but its test must stay green.

- [ ] **Step 9: Commit**

```bash
git add ironlog/generation/program_seed.py docs/program/phase1-seed-source.yaml tests/test_program_seed_yaml_parity.py scripts/reconcile_phase1.py deploy/migrations/013_phase1_reconciliation.sql
git commit -m "feat(seed): reconcile program_seed to authoritative YAML (D1-D6) + YAML-parity keystone"
```

---

### Task 4: MovementState baseline seeding (day-scoped, calibrated)

**Files:**
- Create: `ironlog/generation/baseline_seed.py`
- Test: `tests/test_baseline_seed.py`

**Interfaces:**
- Consumes: seeded program (Task 3) + BandPair labels (`#0 Orange`).
- Produces: `seed_movement_baselines(db: Session) -> None` — upserts one `MovementState(movement_id, day_id=<day_role>)` per loaded slot with `current_load` / `assist_level` / (`ht_plates` + `ht_band_config`=[orange_id]), `calibration_status=CalibrationStatus.MEASURED`. Task 7 calls it.

**BASELINES (exact — keyed by slot_id; `day_role` resolved from the slot's tier→ProgramDay):**

| slot_id | field | value |
|---|---|---|
| d1_t1 | current_load | 165 |
| d1_t2a | current_load | 170 |
| d1_t2b | current_load | 55 |
| d1_t2c | assist_level | 25 |
| d1_t3b | current_load | 12.5 |
| d1_t3c | current_load | 60 |
| d1_t4a | current_load | 100 |
| d1_t4c | current_load | 10 |
| d2_t1 | current_load | 260 |
| d2_t1b | ht_plates + ht_band_config | 180 + [Orange] |
| d2_t2a | assist_level | 20 |
| d2_t2b | current_load | 180 |
| d2_t3a | current_load | 25 |
| d2_t3b | current_load | 25 |
| d4_t2a | current_load | 35 |
| d4_t2b | current_load | 40 |
| d4_t2c | assist_level | 10 |
| d4_t3a | current_load | 10 |
| d4_t3b | current_load | 70 |
| d5_t1 | current_load | 255 |
| d5_t1b | ht_plates + ht_band_config | 205 + [Orange] |
| d5_t2a | current_load | 30 |
| d5_t2b | current_load | 180 |
| d5_t2c | assist_level | 25 |
| d5_t3a | current_load | 20 |
| d5_t3b | assist_level | 20 |
| d5_t3c | current_load | 30 |
| d5_t3d | current_load | 245 |
| d6_g1b | current_load | 150 |
| d6_g1c | ht_plates + ht_band_config | 155 + [Orange] |
| d6_g2a | current_load | 90 |
| d6_g2b | current_load | 30 |
| d6_g2c | current_load | 10 |
| d6_g3a | current_load | 30 |
| d6_g3b | current_load | 60 |
| d6_g3c | current_load | 105 |

Bodyweight/rolling slots — seed NO load (create no MovementState, or a bare needs-calibration-free row): `d1_t3a`, `d1_t4b`, `d2_t2a`(assist only), `d4_t1`, `d4_t3c`, `d6_g1a`. (Assist-only slots create a row with `assist_level` set.)

- [ ] **Step 1: Write the failing test**

```python
def test_baselines_seeded_day_scoped(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.models.library import MovementState, Movement, BandPair
    from ironlog.models.program import TierExercise
    from sqlmodel import select
    seed_movement_baselines(gen_db)
    states = gen_db.exec(select(MovementState)).all()
    by_key = {(s.movement_id, s.day_id): s for s in states}
    te = {t.slot_id: t for t in gen_db.exec(select(TierExercise)).all()}
    # scalar load lands on the right (movement, day)
    d1t1 = te["d1_t1"]
    assert by_key[(d1t1.movement_id, "D1 Upper Push")].current_load == 165
    # HT gets plates + band config = [orange id]
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    d6ht = te["d6_g1c"]
    st = by_key[(d6ht.movement_id, "D6 Weak Points")]
    assert st.ht_plates == 155 and st.ht_band_config == [orange.id]
    # three independent HT tracks exist (D2/D5/D6), NOT one collapsed row
    ht_rows = [s for s in states if s.ht_plates is not None]
    assert {s.ht_plates for s in ht_rows} == {180, 205, 155}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_baseline_seed.py -q'`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `seed_movement_baselines`**

```python
"""baseline_seed.py — seed MovementState calibrated baselines for the go-live.

Keyed on (movement_id, day_id=day_role). Sets scalar current_load / assist_level,
or HT ht_plates + ht_band_config = [orange band id]. Idempotent upsert on
(movement_id, day_id). NO from __future__ import annotations.
"""
from typing import Dict, Optional

from sqlmodel import Session, select

from ironlog.models.enums import CalibrationStatus
from ironlog.models.library import BandPair, MovementState
from ironlog.models.program import ProgramDay, Tier, TierExercise

# slot_id -> ("load"|"assist"|"ht", value, band_label_or_None)
BASELINES = {
    "d1_t1": ("load", 165, None), "d1_t2a": ("load", 170, None),
    "d1_t2b": ("load", 55, None), "d1_t2c": ("assist", 25, None),
    "d1_t3b": ("load", 12.5, None), "d1_t3c": ("load", 60, None),
    "d1_t4a": ("load", 100, None), "d1_t4c": ("load", 10, None),
    "d2_t1": ("load", 260, None), "d2_t1b": ("ht", 180, "#0 Orange"),
    "d2_t2a": ("assist", 20, None), "d2_t2b": ("load", 180, None),
    "d2_t3a": ("load", 25, None), "d2_t3b": ("load", 25, None),
    "d4_t2a": ("load", 35, None), "d4_t2b": ("load", 40, None),
    "d4_t2c": ("assist", 10, None), "d4_t3a": ("load", 10, None),
    "d4_t3b": ("load", 70, None),
    "d5_t1": ("load", 255, None), "d5_t1b": ("ht", 205, "#0 Orange"),
    "d5_t2a": ("load", 30, None), "d5_t2b": ("load", 180, None),
    "d5_t2c": ("assist", 25, None), "d5_t3a": ("load", 20, None),
    "d5_t3b": ("assist", 20, None), "d5_t3c": ("load", 30, None),
    "d5_t3d": ("load", 245, None),
    "d6_g1b": ("load", 150, None), "d6_g1c": ("ht", 155, "#0 Orange"),
    "d6_g2a": ("load", 90, None), "d6_g2b": ("load", 30, None),
    "d6_g2c": ("load", 10, None), "d6_g3a": ("load", 30, None),
    "d6_g3b": ("load", 60, None), "d6_g3c": ("load", 105, None),
}


def _day_role_for_tier(db: Session, tier: Tier) -> str:
    pd = db.exec(select(ProgramDay).where(ProgramDay.id == tier.program_day_id)).one()
    return pd.day_role


def _upsert(db: Session, movement_id: int, day_id: str) -> MovementState:
    st = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            MovementState.day_id == day_id,
        )
    ).first()
    if st is None:
        st = MovementState(movement_id=movement_id, day_id=day_id)
        db.add(st)
    return st


def seed_movement_baselines(db: Session) -> None:
    tes = {t.slot_id: t for t in db.exec(select(TierExercise)).all()}
    tiers = {t.id: t for t in db.exec(select(Tier)).all()}
    bands = {b.label: b.id for b in db.exec(select(BandPair)).all()}
    for slot_id, (kind, value, band_label) in BASELINES.items():
        te = tes.get(slot_id)
        if te is None:
            raise ValueError(f"baseline slot_id not seeded: {slot_id}")
        day_id = _day_role_for_tier(db, tiers[te.tier_id])
        st = _upsert(db, te.movement_id, day_id)
        st.calibration_status = CalibrationStatus.MEASURED
        if kind == "load":
            st.current_load = value
        elif kind == "assist":
            st.assist_level = value
        elif kind == "ht":
            band_id = bands.get(band_label)
            if band_id is None:
                raise ValueError(f"band not seeded: {band_label}")
            st.ht_plates = value
            st.ht_band_config = [band_id]
    db.commit()
```

- [ ] **Step 4: Run the test**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_baseline_seed.py -q'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ironlog/generation/baseline_seed.py tests/test_baseline_seed.py
git commit -m "feat(seed): day-scoped MovementState baseline seeding (loads + HT band-composite)"
```

---

### Task 5: Day-scope generation states in context.py

**Files:**
- Modify: `ironlog/generation/context.py` (~line 307, the `states` dict comprehension)
- Test: `tests/test_generation_day_scoped_state.py`

**Interfaces:**
- Consumes: baselines from Task 4 (per-day rows).
- Produces: `GenerationContext.movement_states` filtered to the day being generated — rows where `day_id == day_role OR day_id IS NULL`, so per-day HT/accessory tracks don't collide.

- [ ] **Step 1: Write the failing test**

```python
def test_ht_load_is_day_scoped(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.generation.loop import generate_session
    seed_movement_baselines(gen_db)
    # HT differs by day: D2=180, D5=205, D6=155. Each day's session must show its own.
    for role, plates in [("D2 Lower A", 180), ("D5 Lower B", 205), ("D6 Weak Points", 155)]:
        sess = generate_session(role, gen_db)
        ht_sets = [ps for g in sess.groups for ex in g.exercises
                   for ps in ex.planned_sets if ps.target_plates is not None]
        assert ht_sets, f"{role}: no HT set with plates"
        assert all(ps.target_plates == plates for ps in ht_sets), f"{role} expected {plates}"
```

(Confirm `generate_session(day_role, db)` signature in `ironlog/generation/loop.py:123` — pass the session db.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_generation_day_scoped_state.py -q'`
Expected: FAIL (movement_id-keyed states collide — wrong plates on ≥1 day)

- [ ] **Step 3: Filter states by day_role**

`resolve_context` (context.py:~290) receives `day_role`. Change the `states` comprehension (line ~307):

```python
    from sqlalchemy import or_, col
    states: Dict[int, MovementState] = {
        s.movement_id: s
        for s in db.exec(
            select(MovementState).where(
                or_(MovementState.day_id == day_role,
                    col(MovementState.day_id).is_(None))
            )
        ).all()
    }
```

If the same movement_id has BOTH a day-scoped and a NULL legacy row, prefer the day-scoped one (iterate NULL rows first, then day-scoped, so day-scoped wins last-write). Confirm `day_role` is in scope in `resolve_context`; if the second context builder at context.py:200 also builds states for generation, apply the same filter there. Do NOT change the composite-key write path.

- [ ] **Step 4: Run the test + the full generation suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_generation_day_scoped_state.py -q && .venv/bin/pytest tests/ -k generat -q'`
Expected: PASS (and no regression in generation tests)

- [ ] **Step 5: Commit**

```bash
git add ironlog/generation/context.py tests/test_generation_day_scoped_state.py
git commit -m "fix(generation): day-scope MovementState load (movement_id,day_id) so per-day tracks don't collide"
```

---

### Task 6: Test-data reset routine

**Files:**
- Modify: `ironlog/generation/baseline_seed.py` (add `reset_transactional_and_state`)
- Test: `tests/test_baseline_seed.py`

**Interfaces:**
- Produces: `reset_transactional_and_state(db: Session) -> None` — deletes all `SetLog`, `ExerciseSurvey`, `Note`, `GenerationLog`, `Session`, `E1rmHistory` rows; clears `MovementState` derived fields (`e1rm`, `e1rm_updated_at`, `consecutive_*`, `stall_signal`, `active_rule`, `unassisted_max_rolling`) WITHOUT touching seeded baselines (`current_load`/`assist_level`/`ht_*`); resets `EngineState` (phase CUT, keep bodyweight). Task 7 calls it before baseline seeding on a non-fresh DB.

- [ ] **Step 1: Write the failing test**

```python
def test_reset_clears_transactional_keeps_baselines(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines, reset_transactional_and_state
    from ironlog.models.library import MovementState
    from ironlog.models.session import SetLog
    from sqlmodel import select
    seed_movement_baselines(gen_db)
    # simulate logged state
    st = gen_db.exec(select(MovementState)).first()
    st.e1rm = 300.0; st.consecutive_advance_count = 4; st.unassisted_max_rolling = 9
    gen_db.add(SetLog(movement_id=st.movement_id, weight=100, reps=8))  # minimal valid row
    gen_db.commit()
    reset_transactional_and_state(gen_db)
    assert gen_db.exec(select(SetLog)).all() == []
    st2 = gen_db.exec(select(MovementState).where(MovementState.id == st.id)).one()
    assert st2.e1rm is None and st2.consecutive_advance_count == 0 and st2.unassisted_max_rolling is None
    assert st2.current_load is not None or st2.assist_level is not None or st2.ht_plates is not None  # baseline kept
```

(Adjust `SetLog(...)` kwargs to the real required columns — check `ironlog/models/session.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_baseline_seed.py::test_reset_clears_transactional_keeps_baselines -q'`
Expected: FAIL (function missing)

- [ ] **Step 3: Implement `reset_transactional_and_state`**

```python
def reset_transactional_and_state(db):
    from ironlog.models.session import SetLog, ExerciseSurvey, Session as WorkoutSession
    from ironlog.models.library import E1rmHistory, EngineState, MovementState
    from ironlog.models.program import Note  # confirm Note's module
    from ironlog.models.enums import Phase
    from sqlmodel import select, delete
    # GenerationLog import from its real module
    for model in (SetLog, ExerciseSurvey, E1rmHistory):
        db.exec(delete(model))
    # Note / GenerationLog / Session — delete all (confirm exact model imports)
    for st in db.exec(select(MovementState)).all():
        st.e1rm = None; st.e1rm_updated_at = None
        st.consecutive_ceiling_sessions = 0; st.consecutive_failed_progressions = 0
        st.consecutive_advance_count = 0; st.stall_signal = None
        st.active_rule = None; st.unassisted_max_rolling = None
    es = db.exec(select(EngineState)).first()
    if es is not None:
        es.current_phase = Phase.CUT
    db.commit()
```

Implementer: resolve the exact model classes + modules for `Note`, `GenerationLog`, `Session` (grep the codebase); delete all rows of each. Keep `EngineState.bodyweight` as-is.

- [ ] **Step 4: Run the test**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_baseline_seed.py -q'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ironlog/generation/baseline_seed.py tests/test_baseline_seed.py
git commit -m "feat(seed): test-data reset (wipe transactional + derived state, keep baselines)"
```

---

### Task 7: Go-live orchestration script + verify

**Files:**
- Create: `scripts/golive_phase1.py`
- Test: `tests/test_golive_phase1.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `golive(db, *, reset=False)` and a `--verify` CLI. On a fresh DB: `seed.seed()` (library) → `seed_phase1_program(db)` → `seed_movement_baselines(db)`. With `reset=True` (existing DB): `reset_transactional_and_state(db)` first. `--verify` generates D1/D2/D4/D5/D6 and asserts clean structure + seeded loads + HT peak, no needs-calibration on calibrated slots.

- [ ] **Step 1: Write the failing end-to-end test**

```python
def test_golive_all_days_generate_clean(gen_db):
    from scripts.golive_phase1 import verify_all_days
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)
    report = verify_all_days(gen_db)   # returns {day_role: {"loaded_slots": int, "needs_cal": [..]}}
    for role in ("D1 Upper Push", "D2 Lower A", "D4 Upper Pull", "D5 Lower B", "D6 Weak Points"):
        assert report[role]["needs_cal"] == [], f"{role} has needs-calibration slots: {report[role]['needs_cal']}"
        assert report[role]["loaded_slots"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_golive_phase1.py -q'`
Expected: FAIL (script missing)

- [ ] **Step 3: Implement `scripts/golive_phase1.py`**

```python
#!/usr/bin/env python3
"""golive_phase1.py — reseed program + calibrated baselines + reset, with --verify.

Fresh DB: seed library + program + baselines. Existing DB: pass --reset to wipe
transactional/derived state first. DESTRUCTIVE — pre-launch only. NO from __future__.
"""
import argparse

from sqlmodel import Session, select

from ironlog.db import engine
from ironlog.generation.baseline_seed import (
    reset_transactional_and_state, seed_movement_baselines,
)
from ironlog.generation.loop import generate_session
from ironlog.generation.program_seed import seed_phase1_program

TRAINING_DAYS = ["D1 Upper Push", "D2 Lower A", "D4 Upper Pull", "D5 Lower B", "D6 Weak Points"]


def verify_all_days(db):
    report = {}
    for role in TRAINING_DAYS:
        sess = generate_session(role, db)
        loaded = 0
        needs_cal = []
        for g in sess.groups:
            for ex in g.exercises:
                # a set is "loaded" if it has target_load or target_plates
                has_load = any(getattr(ps, "target_load", None) is not None
                               or getattr(ps, "target_plates", None) is not None
                               for ps in ex.planned_sets)
                if has_load:
                    loaded += 1
                elif getattr(ex, "needs_calibration", False):
                    needs_cal.append(ex.movement_name)
        report[role] = {"loaded_slots": loaded, "needs_cal": needs_cal}
    return report


def golive(db, reset=False):
    if reset:
        reset_transactional_and_state(db)
    seed_phase1_program(db)
    seed_movement_baselines(db)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe transactional/derived state first (existing DB)")
    ap.add_argument("--verify", action="store_true", help="generate + check all days, no writes")
    args = ap.parse_args()
    with Session(engine) as db:
        if args.verify:
            import json
            print(json.dumps(verify_all_days(db), indent=2))
            return
        golive(db, reset=args.reset)
        print("go-live seed complete")


if __name__ == "__main__":
    main()
```

Implementer: align `verify_all_days`'s "needs calibration" detection with how the assembler actually flags it (grep for `needs_calibration` / the needs-cal marker on `PlannedExercise`); the exact attribute name matters. Keep the verify pass read-only.

- [ ] **Step 4: Run the end-to-end test + FULL suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`
Expected: PASS — full suite green (~433 baseline + new tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/golive_phase1.py tests/test_golive_phase1.py
git commit -m "feat(golive): phase-1 go-live orchestration script + all-days verify"
```

---

## Go-Live Execution (MANUAL, GATED — not an automated task)

After all tasks merge and the full suite is green, the destructive live run is performed **only on explicit user go**, backup-first:

1. **Backup** the live DB: `ssh myflix 'cd ~/projects/IronLog-V2 && cp ironlog.db ironlog.db.bak-pre-golive-$(date +%Y%m%d-%H%M)'`.
2. **Fresh reseed** (the live DB is disposable pre-launch): move the old DB aside, then `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -m ironlog.seed && .venv/bin/python scripts/golive_phase1.py'` (library seed → program → baselines). Apply migration 023 if seeding onto a migrated DB rather than a fresh create_all.
3. **Verify:** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python scripts/golive_phase1.py --verify'` — every day loaded, no needs-cal, HT peaks correct.
4. **Restart** `ironlogv2.service`; hit `/programs` + a `generate` for D6 to smoke-test.
5. This is the point-of-no-return: after real Week-1 logging, backup/pull-before-push discipline resumes.

---

## Self-Review Notes

- **Spec coverage:** IN-scope items from the reconciliation design — D1–D6 structure to YAML (Task 3), per-movement rules via schemes (Task 3), MovementState baselines incl. HT band-config (Task 4), test-data reset (Task 6), go-live verification (Task 7) — all covered. Single-leg Scout Meso-2 (Task 1+2+3). Day-scoping engine fix (Task 5).
- **Deferred/flagged:** none deferred (single-leg built per decision). The MesoRotation rep-override is minimal (nullable columns).
- **Type consistency:** `seed_movement_baselines`, `reset_transactional_and_state`, `golive`, `verify_all_days` names used consistently across Tasks 4/6/7. `_add_mr` gains `rep_low`/`rep_high` in Task 3, defined against the model columns from Task 2.
- **Ordering:** Task 1 (movements) → Task 2 (meso schema) → Task 3 (seeder, depends on 1+2) → Task 4 (baselines, depends on 3's slots) → Task 5 (day-scoping, depends on 4's rows) → Task 6 (reset) → Task 7 (orchestration).
