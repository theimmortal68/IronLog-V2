# Progression Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic load-advancement engine — after each logged session, advance every movement's earned state (tier / assist / rep-target / body-position) per its assigned rule and emit a typed stall signal, all through `run_analysis`, never writing `current_load` (Option-C write boundary).

**Architecture:** A new pure module `ironlog/engine/advance.py` holds a `ProgressionRule`-keyed dispatch over per-rule advance functions, reusing the `progression.py` primitives + the increment ladder. `run_analysis`/`apply_analysis` (the existing single write-point) call it at log-time and persist the *earned* state — the tier index (which `current_load` derives from at generation), assist/rep/position, the confirmation streak, `active_rule`, and `stall_signal`. `commit_session` remains the sole writer of `current_load` at approval (Fork 7c). The schema change ships first, standalone, so all rule logic builds on a stable `(movement_id, day_id)` composite key.

**Tech Stack:** Python 3 / FastAPI / SQLModel / SQLite; pytest on myflix; SQL migrations under `deploy/migrations/`.

## Global Constraints

- **NO `from __future__ import annotations`** in any server file.
- **Migration rule:** every migration is single-statement-atomic OR fully idempotent (`IF NOT EXISTS` / guarded `WHERE`); the parity keystone `tests/test_migrations.py::test_chain_matches_create_all` must stay green (migrated schema == `create_all` from the models).
- **Two-writer boundary (Option C):** the engine writes bookkeeping via `run_analysis`/`apply_analysis` and **NEVER `current_load`**; `commit_session` (`ironlog/generation/loop.py`) stays the sole writer of `current_load` at approval.
- **`engine/` stays pure** deterministic logic (no HTTP, no DB) — `advance.py` takes plain inputs and returns a result object; persistence wires it.
- Repo architecture invariants (its own `CLAUDE.md`): rules-dispose/model-proposes, definition-vs-state, planned-vs-logged, objective gating — do not violate.
- Tests run on myflix: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`. Baseline: current `main` (~298+ passing).
- **Scope:** mechanism only, TDD'd with synthetic fixtures. OUT: seeding live per-movement config, pull-up cross-day structural transitions, meso rotation, HT band-composite (spec §0).

## File Structure

- `ironlog/models/enums.py` — add `ProgressionRule`, `StallType`, `StallSeverity`, `BodyPosition` enums.
- `ironlog/models/library.py` — `MovementState`: new fields + `(movement_id, day_id)` composite unique key; `Movement`: `progression_rule` + ladder-config fields.
- `deploy/migrations/016_progression_engine_schema.sql` — the additive migration.
- `ironlog/engine/advance.py` (new) — `ProgressionRule` dispatch + per-rule advance functions + `AdvanceResult`. Pure.
- `ironlog/engine/stall.py` — add the typed-signal builder over the existing `detect_stall`.
- `ironlog/persistence/run_analysis.py` + `ironlog/persistence/apply.py` — wire the engine + persist earned state; guardrail.
- `tests/test_progression_*.py`, `tests/test_stall_signal.py`, `tests/test_write_boundary.py`.

---

## Task 1: Schema + migration (STANDALONE GATE — build nothing else until this is green)

**Files:**
- Modify: `ironlog/models/enums.py` (new enums), `ironlog/models/library.py` (`MovementState` fields + composite key; `Movement` config fields)
- Create: `deploy/migrations/016_progression_engine_schema.sql`
- Test: `tests/test_migrations.py::test_chain_matches_create_all` (existing keystone — must stay green), `tests/test_progression_schema.py` (new)

**Interfaces:**
- Produces (consumed by every later task): `MovementState.day_id: Optional[str]`, `.consecutive_advance_count: int` (default 0), `.active_rule: Optional[str]`, `.current_body_position: Optional[str]`, `.stall_signal: Optional[dict]` (JSON column), `.unassisted_max_rolling: Optional[int]`; composite unique `(movement_id, day_id)`. `Movement.progression_rule: Optional[str]`, `.assist_ladder: Optional[list]` (JSON), `.position_ladder: Optional[list]` (JSON), `.rep_ladder: Optional[list]` (JSON). `ProgressionRule` enum values: `RPE_8_STANDARD, SINGLE_SESSION, RULE_DRIVEN, INCLINE_REDUCTION, ASSISTANCE_REDUCTION, REP_LADDER, BODY_POSITION, PULL_UP_ROLLING_MAX, FIXED_LOAD`.

- [ ] **Step 1: Write the enums**

In `ironlog/models/enums.py` (follow the existing `class X(str, Enum)` style):

```python
class ProgressionRule(str, Enum):
    RPE_8_STANDARD = "RPE_8_STANDARD"
    SINGLE_SESSION = "SINGLE_SESSION"
    RULE_DRIVEN = "RULE_DRIVEN"
    INCLINE_REDUCTION = "INCLINE_REDUCTION"
    ASSISTANCE_REDUCTION = "ASSISTANCE_REDUCTION"
    REP_LADDER = "REP_LADDER"
    BODY_POSITION = "BODY_POSITION"
    PULL_UP_ROLLING_MAX = "PULL_UP_ROLLING_MAX"
    FIXED_LOAD = "FIXED_LOAD"


class StallType(str, Enum):
    FAILED_PROGRESSION = "FAILED_PROGRESSION"
    PLATEAU = "PLATEAU"
    REGRESSION = "REGRESSION"


class StallSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BodyPosition(str, Enum):
    TUCK = "tuck"
    SINGLE_LEG_EXTENDED = "single_leg_extended"
    STRADDLE = "straddle"
    FULL = "full"
```

- [ ] **Step 2: Write the failing schema test**

```python
# tests/test_progression_schema.py
from sqlmodel import SQLModel, Session, create_engine, select
from ironlog.models.library import MovementState, Movement

def test_movementstate_has_new_progression_fields():
    ms = MovementState(movement_id=1, day_id="d2", consecutive_advance_count=0)
    for f in ("day_id", "consecutive_advance_count", "active_rule",
              "current_body_position", "stall_signal", "unassisted_max_rolling"):
        assert hasattr(ms, f), f
    mv = Movement(name="X", pattern="squat")  # follow the model's real required args
    for f in ("progression_rule", "assist_ladder", "position_ladder", "rep_ladder"):
        assert hasattr(mv, f), f

def test_movementstate_composite_key_allows_same_movement_two_days():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(MovementState(movement_id=1, day_id="d2"))
        s.add(MovementState(movement_id=1, day_id="d5"))  # same movement, different day → OK
        s.commit()
        assert len(s.exec(select(MovementState).where(MovementState.movement_id == 1)).all()) == 2
```

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_progression_schema.py -q'` → FAIL (fields/constraint absent).

- [ ] **Step 3: Update the models**

In `ironlog/models/library.py`, extend `MovementState` (keep all existing fields) — replace the `movement_id` `unique=True` with a table-level composite unique constraint, and add the new columns (all nullable/defaulted, `server_default` where a NOT-NULL default is needed, matching the existing `consecutive_failed_progressions` pattern):

```python
from sqlalchemy import Column, JSON, UniqueConstraint, text

class MovementState(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("movement_id", "day_id", name="uq_movementstate_movement_day"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)   # drop unique=True
    day_id: Optional[str] = Field(default=None, index=True)
    # ... all existing fields unchanged ...
    consecutive_advance_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    active_rule: Optional[str] = None
    current_body_position: Optional[str] = None
    unassisted_max_rolling: Optional[int] = None
    stall_signal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
```

On `Movement`, add:

```python
    progression_rule: Optional[str] = None
    assist_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
    position_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
    rep_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
```

- [ ] **Step 4: Write migration 016**

`deploy/migrations/016_progression_engine_schema.sql` — additive columns are single-statement `ALTER TABLE ADD COLUMN` (SQLite: atomic per statement). The unique-constraint change needs the index rebuilt idempotently. Model on the existing migrations' comment style + idempotency:

```sql
-- 016_progression_engine_schema.sql — progression-engine state + per-movement rule config.
-- Additive columns (ADD COLUMN is atomic in SQLite). The MovementState unique key moves
-- from (movement_id) to (movement_id, day_id): drop the old auto unique index, add day_id,
-- backfill it from each state's originating session, create the composite unique index.
-- Idempotent: ADD COLUMN guarded by the runner's per-file once-semantics; index ops use IF (NOT) EXISTS.
ALTER TABLE movementstate ADD COLUMN day_id VARCHAR;
ALTER TABLE movementstate ADD COLUMN consecutive_advance_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE movementstate ADD COLUMN active_rule VARCHAR;
ALTER TABLE movementstate ADD COLUMN current_body_position VARCHAR;
ALTER TABLE movementstate ADD COLUMN unassisted_max_rolling INTEGER;
ALTER TABLE movementstate ADD COLUMN stall_signal JSON;
ALTER TABLE movement ADD COLUMN progression_rule VARCHAR;
ALTER TABLE movement ADD COLUMN assist_ladder JSON;
ALTER TABLE movement ADD COLUMN position_ladder JSON;
ALTER TABLE movement ADD COLUMN rep_ladder JSON;
-- Backfill day_id: the day_role of the most-recent session that logged each movement.
-- (Existing rows are single-day per movement pre-composite, so this is unambiguous.)
UPDATE movementstate SET day_id = (
    SELECT s.day_role FROM setlog sl JOIN session s ON s.id = sl.session_id
    WHERE sl.movement_id = movementstate.movement_id
    ORDER BY s.id DESC LIMIT 1
) WHERE day_id IS NULL;
DROP INDEX IF EXISTS ix_movementstate_movement_id;   -- the old unique index (confirm its real name)
CREATE UNIQUE INDEX IF NOT EXISTS uq_movementstate_movement_day ON movementstate (movement_id, day_id);
CREATE INDEX IF NOT EXISTS ix_movementstate_movement_id ON movementstate (movement_id);
CREATE INDEX IF NOT EXISTS ix_movementstate_day_id ON movementstate (day_id);
```

**Important:** confirm the actual auto-generated unique index name for the old `movement_id` unique (inspect a fresh DB: `SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='movementstate'`) and drop that exact name. If the parity test compares index definitions, make the model's `UniqueConstraint` name (`uq_movementstate_movement_day`) match the migration's index name so `create_all` and the migration chain converge.

- [ ] **Step 5: Run the schema test + the parity keystone**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_progression_schema.py tests/test_migrations.py -q'`
Expected: PASS — including `test_chain_matches_create_all` (migrated schema == models). Iterate the migration until parity is green (this is the whole point of isolating Task 1).

- [ ] **Step 6: Full suite (no regressions from the model change)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` → all green. Any code that queried `MovementState` by `movement_id` alone still works (day_id nullable); note for later tasks that engine queries key on the composite.

- [ ] **Step 7: Commit**

```bash
git -C ~/projects/IronLog-V2 add ironlog/models/enums.py ironlog/models/library.py deploy/migrations/016_progression_engine_schema.sql tests/test_progression_schema.py
git -C ~/projects/IronLog-V2 commit -m "feat(engine): progression-engine schema — MovementState state fields + (movement_id,day_id) composite key + per-movement rule config (migration 016)"
```

**→ STANDALONE GATE. Do not start Task 2 until parity + full suite are green and this task is reviewed.**

---

## Task 2: Rule-dispatch core + RPE-8 standard

**Files:**
- Create: `ironlog/engine/advance.py`
- Test: `tests/test_progression_rpe8.py`

**Interfaces:**
- Consumes: `MovementState` fields (Task 1); `ironlog/engine/progression.py` `step_down_tier`; `Movement.increment_ladder`.
- Produces: `SessionPerf` input dataclass `{hit_target: bool, max_rpe: float, all_sides_cleared: bool}` (per movement, derived by the caller from `SetLog`s); `AdvanceResult` dataclass with the *earned deltas* — `new_tier: Optional[int]`, `new_assist_level: Optional[float]`, `new_rep_target: Optional[int]`, `new_body_position: Optional[str]`, `consecutive_advance_count: int`, `active_rule: str`, `advanced: bool`; and `advance(rule: ProgressionRule, state: MovementState, perf: SessionPerf, movement: Movement, confirmation_window: int) -> AdvanceResult`. `AdvanceResult` carries only earned state — NEVER `current_load` (§ Option C).

- [ ] **Step 1: Write the failing tests** (spec §8 RPE-8 cases)

```python
# tests/test_progression_rpe8.py
from ironlog.engine.advance import advance, SessionPerf, AdvanceResult
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def _mv(): return Movement(name="Bench", pattern="press", increment_ladder=[5, 5, 5])
def _st(tier=0, streak=0): return MovementState(movement_id=1, day_id="d1",
    current_increment_tier=tier, consecutive_advance_count=streak)

def test_t1_advances_in_one_clean_session():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=0),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True), _mv(), confirmation_window=1)
    assert r.advanced is True and r.new_tier == 1 and r.consecutive_advance_count == 0

def test_t2_needs_two_clean_sessions():
    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)
    r1 = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=0), perf, _mv(), confirmation_window=2)
    assert r1.advanced is False and r1.consecutive_advance_count == 1
    r2 = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=1), perf, _mv(), confirmation_window=2)
    assert r2.advanced is True and r2.new_tier == 1 and r2.consecutive_advance_count == 0

def test_streak_resets_on_missed_reps():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=1),
                SessionPerf(hit_target=False, max_rpe=8.0, all_sides_cleared=True), _mv(), confirmation_window=2)
    assert r.advanced is False and r.consecutive_advance_count == 0

def test_no_advance_when_rpe_over_8():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=1),
                SessionPerf(hit_target=True, max_rpe=9.0, all_sides_cleared=True), _mv(), confirmation_window=2)
    assert r.advanced is False and r.consecutive_advance_count == 0
```

Run → FAIL (module absent).

- [ ] **Step 2: Implement the dispatch + RPE-8**

```python
# ironlog/engine/advance.py
from dataclasses import dataclass
from typing import Optional
from ..models.enums import ProgressionRule

@dataclass
class SessionPerf:
    hit_target: bool          # all working sets hit rep_high (both sides for unilateral)
    max_rpe: float            # highest RPE across working sets
    all_sides_cleared: bool   # unilateral AND-gate (True for bilateral)

@dataclass
class AdvanceResult:
    advanced: bool
    active_rule: str
    consecutive_advance_count: int
    new_tier: Optional[int] = None
    new_assist_level: Optional[float] = None
    new_rep_target: Optional[int] = None
    new_body_position: Optional[str] = None

def _clean(perf: SessionPerf) -> bool:
    return perf.hit_target and perf.max_rpe <= 8.0 and perf.all_sides_cleared

def _rpe8(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.RPE_8_STANDARD.value
    if not _clean(perf):
        return AdvanceResult(False, rule, 0)                      # any miss resets the streak
    streak = state.consecutive_advance_count + 1
    if streak >= window:
        ladder_len = len(movement.increment_ladder or [])
        # advance one tier toward the top of the ladder (bounded); reuse the ladder for rounding
        new_tier = min(state.current_increment_tier + 1, max(ladder_len - 1, state.current_increment_tier + 1))
        return AdvanceResult(True, rule, 0, new_tier=new_tier)
    return AdvanceResult(False, rule, streak)

_DISPATCH = {ProgressionRule.RPE_8_STANDARD: _rpe8}

def advance(rule, state, perf, movement, confirmation_window) -> AdvanceResult:
    fn = _DISPATCH.get(rule)
    if fn is None:
        # fallback invariant: unknown/unhandled rule → no change (spec §9)
        return AdvanceResult(False, getattr(rule, "value", str(rule)), state.consecutive_advance_count)
    return fn(state, perf, movement, confirmation_window)
```

Run the tests → PASS. Commit `feat(engine): advance dispatch + RPE-8 standard rule`.

*(Later tasks register more rules in `_DISPATCH`. Keep `advance()` and `AdvanceResult` stable — this is the contract Tasks 3–6 build on.)*

---

## Task 3: Special load rules (rule-driven, single-session, ceiling→rep-ladder, rep-ladder, fixed-load)

**Files:** Modify `ironlog/engine/advance.py` (register rules); Test: `tests/test_progression_special.py`

**Interfaces:** Consumes Task 2's `advance`/`AdvanceResult`/`SessionPerf`. Adds to `SessionPerf`: `session_performed: bool`, `last_set_hit_target: bool` (for single-session). Uses `Movement.rep_ladder`, and a per-movement `cap`/ceiling (`Movement.cap`) to detect the ceiling.

- [ ] **Step 1: Failing tests** (spec §8):

```python
# tests/test_progression_special.py
from ironlog.engine.advance import advance, SessionPerf
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def test_hip_thrust_advances_at_rpe9_rule_driven():
    mv = Movement(name="Hip Thrust", pattern="hinge", increment_ladder=[5,5,5], cap=220)
    st = MovementState(movement_id=1, day_id="d2", current_increment_tier=0, current_load=180)
    r = advance(ProgressionRule.RULE_DRIVEN, st,
                SessionPerf(hit_target=True, max_rpe=9.0, all_sides_cleared=True, session_performed=True), mv, 1)
    assert r.advanced is True and r.new_tier == 1   # RPE-exempt: RPE 9 still advances

def test_hip_thrust_transitions_to_rep_ladder_at_cap():
    mv = Movement(name="Hip Thrust", pattern="hinge", increment_ladder=[5], cap=220, rep_ladder=[8,10,12])
    st = MovementState(movement_id=1, day_id="d2", current_load=220, current_increment_tier=0)  # at cap
    r = advance(ProgressionRule.RULE_DRIVEN, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True, session_performed=True), mv, 1)
    assert r.active_rule == ProgressionRule.REP_LADDER.value   # rule transitions at ceiling

def test_belt_squat_rep_ladder_advances_reps_two_session():
    mv = Movement(name="Belt Squat", pattern="squat", rep_ladder=[8,10,12,15], cap=260)
    st = MovementState(movement_id=1, day_id="d2", current_load=260, current_rep_target=8, consecutive_advance_count=1)
    r = advance(ProgressionRule.REP_LADDER, st,
                SessionPerf(hit_target=True, max_rpe=7.0, all_sides_cleared=True, session_performed=True), mv, 2)
    assert r.advanced is True and r.new_rep_target == 10

def test_fixed_load_never_advances():
    mv = Movement(name="Rev Hyper Recovery", pattern="hinge")
    st = MovementState(movement_id=1, day_id="d6", current_load=90)
    r = advance(ProgressionRule.FIXED_LOAD, st,
                SessionPerf(hit_target=True, max_rpe=6.0, all_sides_cleared=True, session_performed=True), mv, 2)
    assert r.advanced is False and r.new_tier is None
```

- [ ] **Step 2: Implement the rules** (register in `_DISPATCH`; each follows spec §1.2/1.3/1.6/1.9):
  - `RULE_DRIVEN`: if `perf.session_performed` and `state.current_load < movement.cap` → advance tier (RPE-exempt, ignore `max_rpe`/`hit_target`). If `state.current_load >= movement.cap` → return `AdvanceResult(advanced=?, active_rule=REP_LADDER, ...)` and delegate to the rep-ladder logic (rep_target starts at `rep_ladder[0]`), persisting the rule transition.
  - `SINGLE_SESSION` (V-Bar): advance one tier iff `perf.last_set_hit_target and perf.max_rpe <= 8` (window 1).
  - `REP_LADDER`: on `_clean(perf)` for `window` sessions, advance `new_rep_target` to the next ladder value (`movement.rep_ladder`); terminal value → maintenance (advanced False, no change, no stall).
  - `FIXED_LOAD`: always `AdvanceResult(False, ..., streak unchanged)`.
  Add the ceiling helper: a movement is "at cap" when `state.current_load is not None and movement.cap is not None and state.current_load >= movement.cap`.

- [ ] **Step 3: Tests pass; commit** `feat(engine): rule-driven/single-session/ceiling→rep-ladder/rep-ladder/fixed-load rules`.

---

## Task 4: Reduction + position rules + pull-up rolling-max tracking

**Files:** Modify `ironlog/engine/advance.py`; Test: `tests/test_progression_reduction.py`

**Interfaces:** Consumes Task 2/3 contract. Adds to `SessionPerf`: `unassisted_set1_reps: Optional[int]` (pull-up). Uses `Movement.assist_ladder`, `Movement.position_ladder`.

- [ ] **Step 1: Failing tests** (spec §8):

```python
# tests/test_progression_reduction.py
from ironlog.engine.advance import advance, SessionPerf
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def test_incline_reduction_two_session_steps_down_ladder():
    mv = Movement(name="Nordic", pattern="hinge", assist_ladder=[20,15,10,5,0])
    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)
    st1 = MovementState(movement_id=1, day_id="d2", assist_level=20, consecutive_advance_count=0)
    r1 = advance(ProgressionRule.INCLINE_REDUCTION, st1, perf, mv, 2)
    assert r1.advanced is False and r1.consecutive_advance_count == 1
    st2 = MovementState(movement_id=1, day_id="d2", assist_level=20, consecutive_advance_count=1)
    r2 = advance(ProgressionRule.INCLINE_REDUCTION, st2, perf, mv, 2)
    assert r2.advanced is True and r2.new_assist_level == 15

def test_body_position_steps_tuck_to_single_leg():
    mv = Movement(name="Dragon Flag", pattern="core", position_ladder=["tuck","single_leg_extended","straddle","full"])
    st = MovementState(movement_id=1, day_id="d4", current_body_position="tuck", consecutive_advance_count=1)
    r = advance(ProgressionRule.BODY_POSITION, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True), mv, 2)
    assert r.advanced is True and r.new_body_position == "single_leg_extended"

def test_unilateral_one_side_fails_no_advance():
    mv = Movement(name="ATG Split Squat", pattern="squat", increment_ladder=[2.5])
    r = advance(ProgressionRule.RPE_8_STANDARD, MovementState(movement_id=1, day_id="d2", consecutive_advance_count=0),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=False), mv, 1)  # one side failed
    assert r.advanced is False

def test_pull_up_rolling_max_tracked_no_cross_day_action():
    mv = Movement(name="Pull-up", pattern="pull")
    st = MovementState(movement_id=1, day_id="d4", unassisted_max_rolling=5)
    r = advance(ProgressionRule.PULL_UP_ROLLING_MAX, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True, unassisted_set1_reps=6), mv, 1)
    assert r.advanced is False   # tracking-only this chunk; the CALLER updates unassisted_max_rolling (see Step 2)
```

- [ ] **Step 2: Implement** (register in `_DISPATCH`, spec §1.4/1.5/1.7/1.8):
  - `INCLINE_REDUCTION` / `ASSISTANCE_REDUCTION`: on `_clean` for `window`, step `assist_level` to the next `assist_ladder` value (returned as `new_assist_level`). For assistance reduction, when the ladder reaches the BW/unassisted terminal, set `active_rule = RPE_8_STANDARD` (BW→loaded transition per spec §1.5).
  - `BODY_POSITION`: on `_clean` for `window`, step `current_body_position` to the next `position_ladder` value (`new_body_position`).
  - `PULL_UP_ROLLING_MAX`: `advanced=False` always (no load/assist change here) but expose the rolling-max update: add a small pure helper `roll_unassisted_max(prev: Optional[int], set1_reps: Optional[int]) -> Optional[int]` (rolling 3-session max — simplest correct: `max(prev or 0, set1_reps or 0)` for the beta) that the persistence layer (Task 6) calls to update `unassisted_max_rolling`. Unit-test that helper.
  - **Unilateral AND** is already handled by `_clean` requiring `perf.all_sides_cleared` — verify each rule routes clean-checks through it.

- [ ] **Step 3: Tests pass; commit** `feat(engine): incline/assistance reduction + body-position + pull-up rolling-max tracking`.

---

## Task 5: Typed stall signal

**Files:** Modify `ironlog/engine/stall.py` (add builder); Test: `tests/test_stall_signal.py`

**Interfaces:** Consumes `detect_stall` + constants + `estimate_e1rm`. Produces `build_stall_signal(movement_id, day_id, consecutive_failed, progress_e1rms, current_load, limiting_muscle) -> Optional[dict]` — the typed signal (`stall_type`, `severity`, `duration_sessions`, `e1rm_trend`, `limiting_muscle`; NO `is_swappable`), or `None` when not stalled.

- [ ] **Step 1: Failing tests** (spec §8):

```python
# tests/test_stall_signal.py
from ironlog.engine.stall import build_stall_signal

def test_failed_progression_low_then_high_severity():
    low = build_stall_signal(1, "d1", consecutive_failed=2, progress_e1rms=[200,201,200],
                             current_load=165, limiting_muscle="chest")
    assert low["stall_type"] == "FAILED_PROGRESSION" and low["severity"] == "low"
    high = build_stall_signal(1, "d1", consecutive_failed=5, progress_e1rms=[200,201,200],
                              current_load=165, limiting_muscle="chest")
    assert high["severity"] == "high"

def test_plateau_from_flat_e1rm_trend():
    sig = build_stall_signal(1, "d1", consecutive_failed=0, progress_e1rms=[205,204,205,203],
                             current_load=165, limiting_muscle="chest")
    assert sig["stall_type"] == "PLATEAU" and sig["severity"] == "medium"

def test_regression_from_negative_trend():
    sig = build_stall_signal(1, "d1", consecutive_failed=0, progress_e1rms=[210,205,198],
                             current_load=165, limiting_muscle="chest")
    assert sig["stall_type"] == "REGRESSION"

def test_no_stall_returns_none():
    assert build_stall_signal(1, "d1", consecutive_failed=0, progress_e1rms=[200,205,212],
                              current_load=165, limiting_muscle="chest") is None

def test_signal_has_no_is_swappable_key():
    sig = build_stall_signal(1, "d1", consecutive_failed=2, progress_e1rms=[200,201,200],
                             current_load=165, limiting_muscle="chest")
    assert "is_swappable" not in sig and sig["limiting_muscle"] == "chest"
```

- [ ] **Step 2: Implement `build_stall_signal`** — call the existing `detect_stall` for the core failed+plateau signal (reuse `STALL_FAILED_THRESHOLD`/`STALL_WINDOW`/`STALL_MIN_SESSIONS`/`STALL_EPSILON_PCT`); layer severity: FAILED low at `>= STALL_FAILED_THRESHOLD`, high at `>= STALL_FAILED_THRESHOLD * severity_multiplier` (define `STALL_FAILED_HIGH_MULT = 2` next to the constants); PLATEAU medium when the trend is flat within `STALL_EPSILON_PCT`, high on an extended flat window; REGRESSION when the trend is negative beyond a small threshold. Return `None` when neither arm fires. Emit `limiting_muscle` as passed (the caller supplies `Movement.primary_muscle`); do NOT add `is_swappable`.

- [ ] **Step 3: Tests pass; commit** `feat(engine): typed stall signal (severity taxonomy over detect_stall, no is_swappable)`.

---

## Task 6: Wire into run_analysis + write-boundary guardrail (Option C)

**Files:** Modify `ironlog/persistence/run_analysis.py`, `ironlog/persistence/apply.py`; Test: `tests/test_write_boundary.py`, `tests/test_run_analysis_progression.py`

**Interfaces:** Consumes `advance()`, `roll_unassisted_max()`, `build_stall_signal()`. Produces: `run_analysis` now advances each logged movement's earned state and persists it via `apply_analysis` — writing `current_increment_tier`, `assist_level`, `current_rep_target`, `current_body_position`, `active_rule`, `consecutive_advance_count`, `unassisted_max_rolling`, `stall_signal`, plus the existing e1rm/tier — and **NEVER `current_load`**.

- [ ] **Step 1: Write the guardrail test FIRST** (the load-bearing invariant):

```python
# tests/test_write_boundary.py
# run_analysis must NEVER change MovementState.current_load. Build a logged session
# (reuse the capture/analysis test fixtures), snapshot every state's current_load,
# run_analysis, and assert current_load is byte-identical while OTHER fields advanced.
def test_run_analysis_never_writes_current_load(analysis_fixture):
    before = {ms.id: ms.current_load for ms in analysis_fixture.states()}
    analysis_fixture.run_analysis()
    after = {ms.id: ms.current_load for ms in analysis_fixture.states()}
    assert after == before, "run_analysis wrote current_load — Fork 7c / Option-C violation"
    # and confirm the engine DID advance earned state on a clean session:
    assert any(ms.consecutive_advance_count > 0 or ms.current_increment_tier > 0
               for ms in analysis_fixture.states())
```

(Adapt `analysis_fixture` to the real analysis test setup — a seeded DB with a logged clean session; follow `tests/test_*analysis*`/capture tests.)

Run → FAIL (engine not wired; earned state not advanced).

- [ ] **Step 2: Wire the engine into `run_analysis`/`apply_analysis`** — for each logged movement: derive `SessionPerf` from its `SetLog`s + the joined `PlannedSet` targets (hit_target = all working sets hit `rep_high`, both sides for unilateral; max_rpe from taps/rpe; session_performed; last_set/unassisted_set1 where relevant); look up its `progression_rule` + `confirmation_window` (T1=1/accessory=2 from the tier/objective); call `advance(...)`; call `build_stall_signal(...)` with `Movement.primary_muscle`; update `unassisted_max_rolling` via `roll_unassisted_max`. Persist the `AdvanceResult` deltas + `stall_signal` through `apply_analysis` (the single write-point) keyed on `(movement_id, day_id)`. **Do not write `current_load`.** Clear `stall_signal` when the movement advanced.

- [ ] **Step 3: Fallback invariant** — wrap each movement's `advance()` in a guard: on exception, leave that movement's state unchanged and log it (spec §9 "a broken engine step reduces to your program yesterday"). Add a test that a raising rule leaves state untouched and doesn't abort the whole analysis.

- [ ] **Step 4: Run guardrail + progression + full suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_write_boundary.py tests/test_run_analysis_progression.py -q'` then the full suite → all green.

- [ ] **Step 5: Commit** `feat(engine): wire progression engine into run_analysis (earned-state writes, current_load-free) + guardrail`.

---

## Verification (Tier A, after all tasks)

`ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` — full suite green. Confirm: the guardrail test proves `run_analysis` never writes `current_load`; parity keystone green; the engine advances earned state on clean sessions and emits stall signals. (No on-device step — server engine; the live per-movement config seed + a real regen is the follow-on seed-reconciliation step, out of this chunk.)

## Routing Plan

| Task | Delegate to | Model |
|---|---|---|
| 1 Schema + migration (standalone gate) | Claude Code Agent subagent | standard (migration judgment) |
| 2 Dispatch core + RPE-8 | Claude Code Agent subagent | standard |
| 3 Special load rules | Claude Code Agent subagent | standard |
| 4 Reduction/position/rolling-max | Claude Code Agent subagent | standard |
| 5 Typed stall signal | Claude Code Agent subagent | standard |
| 6 run_analysis wiring + guardrail | Claude Code Agent subagent | standard (integration) |
| Gate reviews + final whole-branch | Tier A + opus final reviewer | — |

**Delegation ratio: 6/6 implementation tasks delegated (100%).** Tier A reviews each diff at the gate (Task 1 especially — the migration parity), runs the final whole-branch review (opus), and holds the merge. Codex/Gemini read-only → the apply+test substrate is Claude Code subagents.
