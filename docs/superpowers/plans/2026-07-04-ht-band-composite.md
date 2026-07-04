# HT Band-Composite Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load Hip Thrust as plates + a stackable band configuration, where the engine deterministically picks the peak-maximizing setup under the 220 bottom cap — replacing the interim rep-ladder-at-cap.

**Architecture:** A pure `ht_next_setup` search (64 band subsets × plate steps) chooses the smallest-peak-step feasible `(plates, config)`. The assembler prescribes it; `commit_session` persists the setup at approval (Option-C); the validator sums the config's rests against the 220 clamp; the client shows the setup + a reconfigure cue + felt-peak capture that refines single-band models.

**Tech Stack:** Python/FastAPI/SQLModel (server, pytest on myflix); Kotlin/Compose (client, gradlew).

## Global Constraints

- Server: **NO `from __future__ import annotations`.**
- Migration additive/single-statement-or-additive-carve-out + parity keystone `test_chain_matches_create_all` green.
- **Option-C two-writer boundary:** the engine *decides* the HT setup; `commit_session` is the sole writer of `current_load`/`ht_plates`/`ht_band_config` (at approval); `run_analysis` writes bookkeeping only, **never** those fields. `engine/` stays pure (no DB/HTTP in `ht_next_setup`).
- Server tests on myflix: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`. Baseline `main` (~336).
- Client: no new Gradle dependency; `SERVER_BASE_URL=http://192.168.1.7:8000` local-uncommitted.
- Independent of the config-seed reconciliation.

## File Structure

- `ironlog/models/library.py` — `MovementState.ht_band_config`; reseed `BandPair` values.
- `ironlog/models/session.py` — `PlannedSet.band_config`.
- `deploy/migrations/019_ht_band_config.sql` — additive columns.
- `ironlog/engine/band_composite.py` (new) — pure `ht_next_setup` + `config_bottom`/`config_peak` helpers.
- `ironlog/engine/validator.py` — `_check_ht_safety` sums the config.
- `ironlog/generation/assembler.py` + `ironlog/generation/loop.py` — prescribe + persist the setup.
- `ironlog/persistence/…` (submit/analysis path) — single-band felt-peak refinement.
- `ironlog/api/schemas_capture.py` — DTO `band_config`.
- client `CaptureModels.kt` / `CaptureScreen.kt` — DTO + setup display + reconfigure cue + felt-peak input.

---

## Task 1: Schema + band inventory (STANDALONE GATE)

**Files:** Modify `ironlog/models/library.py` (`MovementState.ht_band_config` + reseed `BandPair`), `ironlog/models/session.py` (`PlannedSet.band_config`), `ironlog/api/schemas_capture.py` (DTO); Create `deploy/migrations/019_ht_band_config.sql`; Test `tests/test_band_composite_schema.py`, existing `tests/test_migrations.py`.

**Interfaces produced:** `MovementState.ht_band_config: Optional[list]` (JSON, list of band ids); `PlannedSet.band_config: Optional[list]` (JSON); `PlannedSetOut.band_config: Optional[List[int]]`; `BandPair` rows seeded with the formula values.

- [ ] **Step 1: Failing schema + seed test**

```python
# tests/test_band_composite_schema.py
from sqlmodel import SQLModel, Session, create_engine, select
from ironlog.models.library import MovementState, BandPair
from ironlog.models.session import PlannedSet

def test_new_json_config_fields_exist():
    ms = MovementState(movement_id=1, day_id="d2", ht_band_config=[0, 1])
    assert ms.ht_band_config == [0, 1]
    ps = PlannedSet(session_id=1, set_index=0, band_config=[0])  # follow real required args
    assert ps.band_config == [0]

def test_band_inventory_seeded_from_formula(seeded_db):  # reuse the seed test fixture
    bands = {b.label: (b.bottom_lb, b.peak_lb) for b in seeded_db.exec(select(BandPair)).all()}
    # rest = rated/side x2, peak = rated/side x5
    assert bands["#0 Orange"] == (18, 45)
    assert bands["#1 Red"] == (36, 90)
    assert bands["#4 Black"] == (130, 325)
    assert all(b.usable for b in seeded_db.exec(select(BandPair)).all())
```

- [ ] **Step 2: Run → FAIL** (`ssh myflix '… pytest tests/test_band_composite_schema.py -q'`).

- [ ] **Step 3: Models + reseed**

`MovementState` (add): `ht_band_config: Optional[list] = Field(default=None, sa_column=Column(JSON))`. `PlannedSet` (add): `band_config: Optional[list] = Field(default=None, sa_column=Column(JSON))`. Reseed the six `BandPair` rows at `ironlog/seed.py:684` with the formula table (labels `#0 Orange`…`#5 Purple`; `bottom_lb`=rated×2, `peak_lb`=rated×5; all `usable=True`, `MODELED`): Orange (18,45), Red (36,90), Blue (60,150), Green (80,200), Black (130,325), Purple (190,475).

- [ ] **Step 4: Migration 019** — `deploy/migrations/019_ht_band_config.sql` (additive schema carve-out):

```sql
-- 019_ht_band_config.sql — HT band-composite: stackable band configuration (JSON list of band ids).
-- Purely-additive schema (ADD COLUMN, nullable JSON) -> allowed multi-statement per the README carve-out.
ALTER TABLE movementstate ADD COLUMN ht_band_config JSON;
ALTER TABLE plannedset ADD COLUMN band_config JSON;
```
Plus a data migration for the band values ONLY if the seed's `BandPair` rows are already in prod and need updating — if so, that's a separate idempotent `UPDATE` file (`020_band_values.sql`, single guarded statements) since it's data, not schema. (Prefer: reseed via the seed source; the live DB is reseedable — plan Task 1's live step accordingly.)

- [ ] **Step 5: DTO** — `PlannedSetOut` (schemas_capture.py): add `band_config: Optional[List[int]] = None`; `_serialize_session` populates it from `ps.band_config`. (`target_plates`/`target_felt_peak` already exist on the DTO.)

- [ ] **Step 6: Run schema test + parity + full suite → green**, then commit `feat(ht): band-config schema + inventory reseed (migration 019)`.

**→ STANDALONE GATE: parity + full suite green before Task 2.**

---

## Task 2: `ht_next_setup` pure peak-max search

**Files:** Create `ironlog/engine/band_composite.py`; Test `tests/test_ht_next_setup.py`.

**Interfaces produced:** `Band = namedtuple("Band", "id rest peak")`; `config_bottom(plates, config, by_id)`, `config_peak(plates, config, by_id)`; `ht_next_setup(plates: float, config: list, inventory: List[Band], plate_step: float = 5, clamp: float = 220) -> Tuple[float, list]` — the next `(plates, config)`.

- [ ] **Step 1: Failing tests** (spec §Testing):

```python
# tests/test_ht_next_setup.py
from ironlog.engine.band_composite import ht_next_setup, Band

INV = [Band(0,18,45), Band(1,36,90), Band(2,60,150), Band(3,80,200), Band(4,130,325), Band(5,190,475)]

def test_raise_plates_within_config_when_room():
    # 180 + Orange (bottom 198) -> +5 plates, same config (no reconfigure)
    assert ht_next_setup(180, [0], INV, 5, 220) == (185, [0])

def test_add_band_when_plates_capped():
    # Orange caps at 202 plates (bottom 220). From 202+Orange (peak 247), next needs a reconfigure.
    plates, config = ht_next_setup(202, [0], INV, 5, 220)
    assert (plates + sum(b.peak for b in INV if b.id in config)) > 247   # peak advanced
    assert plates + sum(b.rest for b in INV if b.id in config) <= 220    # legal bottom
    assert len(set(config)) == len(config)                              # each band once

def test_never_exceeds_bottom_clamp():
    plates, config = ht_next_setup(202, [0], INV, 5, 220)
    assert plates + sum(b.rest for b in INV if b.id in config) <= 220

def test_smallest_peak_step():
    # from a capped Orange, the chosen next peak is the least peak strictly greater than current
    cur_peak = 202 + 45
    plates, config = ht_next_setup(202, [0], INV, 5, 220)
    nxt = plates + sum(b.peak for b in INV if b.id in config)
    # no feasible setup has a peak strictly between cur_peak and nxt
    assert nxt > cur_peak
```

- [ ] **Step 2: Run → FAIL** (module absent).

- [ ] **Step 3: Implement** — pure, no DB:

```python
# ironlog/engine/band_composite.py
from collections import namedtuple
from itertools import combinations
from typing import List, Tuple

Band = namedtuple("Band", "id rest peak")

def config_bottom(plates, config, by_id):
    return plates + sum(by_id[b].rest for b in config)

def config_peak(plates, config, by_id):
    return plates + sum(by_id[b].peak for b in config)

def _all_configs(inventory):
    ids = [b.id for b in inventory]
    for k in range(len(ids) + 1):
        for combo in combinations(ids, k):     # each band at most once
            yield list(combo)

def ht_next_setup(plates, config, inventory, plate_step=5, clamp=220) -> Tuple[float, list]:
    by_id = {b.id: b for b in inventory}
    cur_peak = config_peak(plates, config, by_id)
    # 1) prefer raising plates within the current config (no reconfigure)
    if config_bottom(plates + plate_step, config, by_id) <= clamp:
        return (plates + plate_step, list(config))
    # 2) search all subsets for the smallest peak strictly above current
    best = None
    for cfg in _all_configs(inventory):
        srest = sum(by_id[b].rest for b in cfg)
        if srest > clamp:
            continue
        max_plates = int((clamp - srest) // plate_step) * plate_step
        p = 0.0
        while p <= max_plates:
            pk = p + sum(by_id[b].peak for b in cfg)
            if pk > cur_peak:
                key = (pk, len(cfg))   # smallest peak, then fewest bands
                if best is None or key < best[0]:
                    best = (key, (p, list(cfg)))
            p += plate_step
    return best[1] if best else (plates, list(config))
```

- [ ] **Step 4: Run → PASS**, then commit `feat(engine): ht_next_setup peak-max band-config search (pure)`.

---

## Task 3: Validator sums the config

**Files:** Modify `ironlog/engine/validator.py` `_check_ht_safety` (line 245); Test `tests/test_ht_validator_config.py`.

**Interfaces consumed:** `PlannedSet.band_config` (Task 1); `ctx.band_bottom_lb` (dict band_id→rest, existing).

- [ ] **Step 1: Failing tests**

```python
# tests/test_ht_validator_config.py — build a ValidationContext like the existing validator tests
def test_two_band_config_over_clamp_rejected(ht_ctx):
    # plates 200 + Orange(18)+Red(36) = 254 bottom > 220 -> HT_BOTTOM_OVER_LIMIT
    session = _ht_session(target_plates=200, band_config=[0, 1])
    v = _check_ht_safety(session, ht_ctx)
    assert any(x.rule == RuleCode.HT_BOTTOM_OVER_LIMIT for x in v)

def test_legal_config_passes(ht_ctx):
    session = _ht_session(target_plates=150, band_config=[0, 1])  # 150+54=204 <= 220
    assert _check_ht_safety(session, ht_ctx) == []

def test_unregistered_band_in_config_rejected(ht_ctx):
    session = _ht_session(target_plates=100, band_config=[0, 99])  # 99 not registered
    v = _check_ht_safety(session, ht_ctx)
    assert any(x.rule == RuleCode.HT_BAND_NOT_REGISTERED for x in v)
```

- [ ] **Step 2: Run → FAIL** (still reads single `band_pair_id`).

- [ ] **Step 3: Implement** — replace the per-set body to iterate `ps.band_config` (fall back to `[ps.band_pair_id]` if `band_config` is None and `band_pair_id` set, for back-compat): if `ps.target_plates is None or not band_ids: continue`; for each id, if not in `ctx.band_bottom_lb` → `HT_BAND_NOT_REGISTERED` (and skip bottom calc); else `bottom_total = ps.target_plates + sum(ctx.band_bottom_lb[b] for b in band_ids)`; if `> ctx.ht_bottom_clamp` → `HT_BOTTOM_OVER_LIMIT`. Keep REJECT kinds + messages.

- [ ] **Step 4: Run → PASS + full suite green**, commit `feat(validator): HT bottom-clamp sums the band config`.

---

## Task 4: Wire into HT progression + assembler (Option-C)

**Files:** Modify `ironlog/generation/assembler.py` (HT slot prescribes plates+config via `ht_next_setup`), `ironlog/generation/loop.py` (`commit_session` persists `ht_plates`+`ht_band_config`), `ironlog/engine/advance.py` (`_rule_driven`: for COMPOSITE HT, do NOT hand to `_rep_ladder` — the setup is assembler-resolved); Test `tests/test_ht_composite_wiring.py`, `tests/test_ht_write_boundary.py`.

**Interfaces consumed:** `ht_next_setup` (Task 2); `MovementState.ht_plates`/`ht_band_config`; `BandPair` inventory (load from DB into `Band` tuples).

- [ ] **Step 1: Failing tests**

```python
# tests/test_ht_composite_wiring.py
def test_assembled_ht_carries_plates_and_config(seeded_ht_program, db):
    # generate an HT-containing session via the generation fixtures (follow tests/test_generation_*):
    outcome = generate_session("D2 Lower A", db, proposer, week_keyer)
    ht_set = _first_ht_working_set(outcome.assembled)  # helper: HT exercise's first working set
    assert ht_set.target_plates is not None and ht_set.band_config is not None
    peak_by_id = {b.id: b.peak_lb for b in db.exec(select(BandPair)).all()}
    assert ht_set.target_felt_peak == ht_set.target_plates + sum(peak_by_id[b] for b in ht_set.band_config)

# tests/test_ht_write_boundary.py
def test_run_analysis_never_writes_ht_setup(ht_analysis_fixture):
    before = {(m.movement_id): (m.ht_plates, m.ht_band_config) for m in fixture.ht_states()}
    fixture.run_analysis()
    after = {(m.movement_id): (m.ht_plates, m.ht_band_config) for m in fixture.ht_states()}
    assert after == before  # Option-C: only commit_session writes the setup
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**
  - **Assembler HT slot:** when the movement is HT/COMPOSITE, load `BandPair` rows → `Band(id, bottom_lb, peak_lb)` list; read the movement's current `(ht_plates, ht_band_config)` from state (defaults: plates from `current_load`/seed, config from `ht_band_config` or `[ht_band_pair_id]`); call `ht_next_setup(cur_plates, cur_config, inventory)`; set the HT `PlannedSet.target_plates` + `band_config` + `target_felt_peak = config_peak(plates, config, by_id)`. Record the prospective `(plates, config)` for commit.
  - **`commit_session`:** where it writes `current_load` from `prospective_current_loads`, also persist the HT movement's `ht_plates` + `ht_band_config` from the prospective setup (still approval-time, still the sole writer).
  - **`_rule_driven`:** guard the at-cap branch — for a COMPOSITE HT movement, return no-op advance (`AdvanceResult(False, RULE_DRIVEN.value, streak)`) instead of `_rep_ladder`, because the setup progression is assembler-resolved. (Non-composite RULE_DRIVEN unchanged.)
  - **`run_analysis`/`apply`:** confirm they never write `ht_plates`/`ht_band_config` (guardrail test).

- [ ] **Step 4: Run wiring + guardrail + full suite → green**, commit `feat(ht): assembler prescribes band-composite setup; commit persists it (Option-C)`.

---

## Task 5: Single-band felt-peak refinement

**Files:** Modify the submit/analysis path (`ironlog/persistence/…` — the point that processes logged `SetLog`s); Test `tests/test_felt_peak_refine.py`.

**Interfaces consumed:** `SetLog.felt_peak`, `SetLog.band_config`/the session's HT config, `BandPair.peak_lb`/`calibration_status`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_felt_peak_refine.py
def test_single_band_log_refines_band_peak(db_with_ht_log):
    # logged HT: config=[2 Blue], plates=100, felt_peak=255 -> observed band peak = 155 (vs modeled 150)
    refine_from_logged_ht(session_id, db)
    blue = db.get(BandPair, blue_id)
    assert abs(blue.peak_lb - 155) < 10   # moved toward observed via running estimate

def test_multi_band_log_leaves_bands_untouched(db_with_multiband_ht_log):
    before = {b.id: b.peak_lb for b in db.exec(select(BandPair)).all()}
    refine_from_logged_ht(session_id, db)
    after = {b.id: b.peak_lb for b in db.exec(select(BandPair)).all()}
    assert after == before   # can't isolate individual bands in a stack
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** `refine_from_logged_ht(session_id, db)` (called from the submit/analysis path): for each logged HT set whose config has **exactly one** band and a non-null `felt_peak`, `observed = felt_peak - actual_plates`; update that `BandPair.peak_lb` toward `observed` via a running estimate (e.g. EMA `peak_lb = round(0.7*peak_lb + 0.3*observed, 1)`); after **N=3** consistent single-band readings (track a per-band count), flip `calibration_status → MEASURED`. Multi-band sets: skip. Never touch `current_load`/`ht_plates`.

- [ ] **Step 4: Run → PASS + full suite green**, commit `feat(ht): single-band felt-peak refines the band model (MODELED->MEASURED)`.

**→ Server phase complete: full suite green before the client.**

---

## Task 6: Client — setup display + reconfigure cue + felt-peak capture

**Files:** Modify `app/.../data/api/dto/CaptureModels.kt` (`PlannedSetOut.band_config`), `app/.../ui/screens/capture/CaptureScreen.kt` (display + cue + felt-peak input); Test `app/src/test/.../HtSetupLogicTest.kt`.

**Interfaces:** `PlannedSetOut.band_config: List<Int>? = null` (+ existing `target_plates`/`target_felt_peak`); band-id→name map (seed the six labels client-side or fetch `/bands/usable`).

- [ ] **Step 1: Failing unit tests** (pure helpers):

```kotlin
// HtSetupLogicTest.kt
@Test fun reconfigure_cue_fires_when_config_or_plates_change() {
    assertNotNull(htReconfigure(prevPlates=205, prevConfig=listOf(0), plates=166, config=listOf(0,1)))
    assertNull(htReconfigure(prevPlates=205, prevConfig=listOf(0), plates=210, config=listOf(0)))  // same config, plates+, no reconfigure banner? -> decide: fire only on CONFIG change OR any change
}
@Test fun single_band_observed_peak_is_felt_minus_plates() {
    assertEquals(155.0, htObservedPeak(feltPeak=255.0, plates=100.0, config=listOf(2)), 0.001)
    assertNull(htObservedPeak(feltPeak=255.0, plates=100.0, config=listOf(0,1)))  // multi-band -> null
}
```

(Decide in the test: the reconfigure banner fires when the **band config** changes OR the plate count changes vs the prior session — match the spec's "config or plate count differ." Keep the helper pure + total.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — `PlannedSetOut.band_config` DTO; pure helpers `htReconfigure(prevPlates, prevConfig, plates, config): String?` (the "reconfigure to X" text when they differ) and `htObservedPeak(feltPeak, plates, config): Double?` (single-band only). In `CaptureScreen`, render the HT setup line (`205 plates + Orange · peak ~250` using band names + "~" while the peak is modeled), a reconfigure banner at the HT group when the setup changed from the prior session (reuse the shoe/rest cue banner style), and a felt-peak input on HT working sets writing `SetLog.felt_peak`.

- [ ] **Step 4: Run unit tests + `assembleDebug` → green**, commit `feat(capture): HT band-composite setup display + reconfigure cue + felt-peak capture`.

---

## Verification (Tier A)

Full server suite green (incl. parity + the Option-C guardrail); client build + install; phone check: HT shows `plates + band(s) · ~peak`; a forced setup change shows the reconfigure banner; felt-peak input records; validator rejects an over-220 config.

## Routing Plan

| Task | Repo | Delegate to |
|---|---|---|
| 1 Schema + inventory (gate) | server | Claude Code Agent subagent |
| 2 `ht_next_setup` pure search | server | Claude Code Agent subagent |
| 3 Validator sums config | server | Claude Code Agent subagent |
| 4 Wire progression + assembler (Option-C) | server | Claude Code Agent subagent |
| 5 Felt-peak refinement | server | Claude Code Agent subagent |
| 6 Client setup + cue + capture | client | Claude Code Agent subagent |
| Gate reviews + final whole-branch | both | Tier A + opus final reviewer |

**Delegation ratio: 6/6 implementation tasks delegated (100%).** Tier A gate-reviews each diff (Task 1 migration parity + Task 4 Option-C guardrail especially), runs the final whole-branch review, holds the merge. Codex/Gemini read-only → apply+test substrate is Claude Code subagents.
