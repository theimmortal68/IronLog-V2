# Analysis Hook (v0.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the post-session analysis hook — a pure core `engine/analysis.py` that computes an `AnalysisResult` of proposed state deltas, plus a thin `persistence/apply.py` that is the single DB write point. Per the approved design at `docs/superpowers/specs/2026-06-25-analysis-hook-design.md`.

**Architecture:** Pure-compute → thin-applier (the validator's contract applied to writes). `engine/analysis.py` is 100% pure (no DB): `analyze_session(ctx) -> AnalysisResult`. `persistence/apply.py` (a new package) reads the result and writes `MovementState`, resolving all rows first for atomicity. One shared anchor — the best working set by `estimate_e1rm` — drives both the e1RM value and the three-way CEILING/MISS/NEITHER outcome.

**Tech Stack:** Python 3.14, SQLModel domain types (read-only in engine; read+write in the applier), `dataclasses`, `enum.Enum`, pytest 8. Reuses `engine/e1rm.estimate_e1rm` and `engine/progression.step_down_tier`. No new dependencies.

## Global Constraints

Carried verbatim from the approved spec and `~/projects/IronLog-V2/CLAUDE.md`. Every task's requirements implicitly include this section.

- **`engine/analysis.py` is pure logic.** Imports only `dataclasses`, `enum`, `typing`, `..models.enums`, `.e1rm`, `.progression`. No DB / `sqlmodel.Session` / network / LLM / file I/O. The write happens only in `persistence/apply.py`.
- **Do NOT add `from __future__ import annotations`** to any file (repo-wide rule; `persistence/apply.py` imports `MovementState` which has `Relationship`s).
- **One shared anchor:** both the e1RM value and the outcome classification key off the best working set by `estimate_e1rm`. "All sets" would break top-set+backoff detection.
- **Three-way mutually-exclusive outcome** (one `if/elif/else`): MISS (`tap == TOO_HARD` OR `reps < target_reps_low`), CEILING (`reps >= target_reps_high` AND `tap != TOO_HARD`), NEITHER (everything else, incl. anchor lacking a rep range and tap != TOO_HARD). A session can never tick both counters.
- **Measurements always-on; prescription machinery PROGRESS-gated.** e1RM update and phase-gate evaluation run regardless of objective. `consecutive_ceiling`, `consecutive_failed`, and tier step-down compute ONLY when `objective == Objective.PROGRESS`.
- **The hook never writes `current_load`** (generation's sole job — no field for it in the delta) **and never writes `current_phase`** (`phase_transition_available` is report-only; the applier does not consume it).
- **No-qualifying-sets guard:** if no working set qualifies for e1RM (no anchor), `new_e1rm = None` (untouched, never null/zero) AND all counters untouched (`None` in the delta).
- **`step_down_tier(tier, ladder_len, consecutive_fails, threshold=2)`** returns `tier + 1` (a finer rung) after `threshold` consecutive fails, but only if `tier < ladder_len - 1` (won't drop past the finest rung); otherwise returns `tier` unchanged.
- **`None`-means-untouched** on every `MovementStateDelta` field. The applier only writes a field when its delta value is not None.
- **Applier atomicity by construction:** resolve all `MovementState` rows first (a missing row raises before any mutation), then apply + single `commit()`.
- **Test runner is myflix via SSH.** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q [args]'` — workstation pytest imports fail; the venv lives on myflix. Files NFS-sync workstation→myflix instantly.
- **Baseline: 72 tests pass.** After v0.4: 72 + 17 (analysis) + 6 (applier) = 95, all green.

---

## File structure

```
ironlog/models/library.py           MODIFY (Task 1) — MovementState.consecutive_failed_progressions: int = 0
ironlog/engine/analysis.py          NEW (Tasks 2-4) — pure core: dataclasses + analyze_session
ironlog/engine/__init__.py          MODIFY (Task 4) — re-export analyze_session + the dataclasses
ironlog/persistence/__init__.py     NEW (Task 5) — package marker
ironlog/persistence/apply.py        NEW (Task 5) — apply_analysis(result, db)
tests/test_analysis.py              NEW (Tasks 2-4) — ~17 pure-core cases (no DB)
tests/test_apply_analysis.py        NEW (Task 5) — ~6 applier cases (in-memory SQLite)
```

Task decomposition: Task 1 is the schema field (independently testable: existing suite still green). Tasks 2-4 build the pure core incrementally (dataclasses + e1RM; then outcome+gating+tier; then phase gates + re-exports) — each ends with passing analysis tests. Task 5 is the applier (the only DB-writing code, with its own DB-touching test file). Boundaries are where a reviewer could reject one piece while approving its neighbor.

---

### Task 1: Schema field — MovementState.consecutive_failed_progressions

**Files:**
- Modify: `ironlog/models/library.py` — add the field to `MovementState`

**Interfaces:**
- Consumes: nothing.
- Produces: `MovementState.consecutive_failed_progressions: int` (default 0). Read by Task 2-4's context projection; written by Task 5's applier.

- [ ] **Step 1: Confirm baseline is green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'`
Expected: `72 passed`.

- [ ] **Step 2: Add the field**

Edit `ironlog/models/library.py`. Find this line in the `MovementState` class body:

```python
    consecutive_ceiling_sessions: int = 0
```

Add the new field immediately after it:

```python
    consecutive_ceiling_sessions: int = 0
    consecutive_failed_progressions: int = 0           # mirrors ceiling counter; PROGRESS-gated (v0.4)
```

- [ ] **Step 3: Run the suite — confirm no regression**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'`
Expected: `72 passed`. (The additive field breaks nothing; import-side errors would surface here.)

- [ ] **Step 4: Spot-check the field**

Run:
```
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -c "
from ironlog.models.library import MovementState
s = MovementState(movement_id=1)
assert s.consecutive_failed_progressions == 0
print(\"default OK:\", s.consecutive_failed_progressions)"'
```
Expected: `default OK: 0`.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/models/library.py
git commit -m "feat(model): MovementState.consecutive_failed_progressions (v0.4)

Mirror of consecutive_ceiling_sessions: current state (a counter), not
history. Default 0; PROGRESS-gated input to tier step-down. Only schema
change in v0.4. Prerequisite for the analysis hook.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Analysis dataclasses + e1RM update (always-on)

**Files:**
- Create: `ironlog/engine/analysis.py` — the dataclasses + `analyze_session` computing ONLY e1RM for now
- Create: `tests/test_analysis.py` — e1RM cases + factory helpers

**Interfaces:**
- Consumes: `estimate_e1rm` from `.e1rm`; `FeedbackTap`, `Objective`, `Phase` from `..models.enums`.
- Produces:
  - Dataclasses `LoggedSet`, `MovementAnalysisInput`, `EngineStateInput`, `AnalysisContext`, `MovementStateDelta`, `AnalysisResult` (exact fields per the design §4).
  - `analyze_session(ctx: AnalysisContext) -> AnalysisResult` — Task 2 computes e1RM only; Tasks 3-4 extend it.
  - A module-level helper `_best_e1rm_set(logged_sets) -> Optional[tuple[LoggedSet, float]]` returning the anchor set and its e1RM (or None if no set qualifies). Reused for the outcome determination in Task 3.
  - Test factory helpers `make_logged_set`, `make_movement_input`, `make_engine_state`, `make_context` (module-level functions in the test file).

- [ ] **Step 1: Write the e1RM tests + factory helpers**

Create `tests/test_analysis.py`:

```python
"""Tests for ironlog.engine.analysis.analyze_session (v0.4 analysis hook).

Pure-core tests — no DB. The applier (persistence/apply.py) is tested
separately in tests/test_apply_analysis.py.
"""
from ironlog.models.enums import FeedbackTap, Objective, Phase
from ironlog.engine.analysis import (
    AnalysisContext, AnalysisResult, EngineStateInput, LoggedSet,
    MovementAnalysisInput, MovementStateDelta, analyze_session,
)


# ---------------------------------------------------------------------------
# Factory helpers — module-level functions, shared by every test.
# ---------------------------------------------------------------------------

def make_logged_set(
    *,
    actual_load=None, actual_reps=None, feedback_tap=None, is_warmup=False,
    target_rpe=None, target_reps_low=None, target_reps_high=None,
) -> LoggedSet:
    return LoggedSet(
        actual_load=actual_load, actual_reps=actual_reps,
        feedback_tap=feedback_tap, is_warmup=is_warmup,
        target_rpe=target_rpe, target_reps_low=target_reps_low,
        target_reps_high=target_reps_high,
    )


def make_movement_input(
    movement_id=1, *, objective=Objective.PROGRESS, current_tier=0,
    increment_ladder_len=3, consecutive_ceiling_sessions=0,
    consecutive_failed_progressions=0, logged_sets=None,
) -> MovementAnalysisInput:
    return MovementAnalysisInput(
        movement_id=movement_id, objective=objective, current_tier=current_tier,
        increment_ladder_len=increment_ladder_len,
        consecutive_ceiling_sessions=consecutive_ceiling_sessions,
        consecutive_failed_progressions=consecutive_failed_progressions,
        logged_sets=list(logged_sets or []),
    )


def make_engine_state(
    *, current_phase=Phase.CUT, bodyweight=None,
    cut_to_stab_target=213.0, cut_to_stab_tolerance=2.0,
    rhr_down=False, sleep_ok=False, no_rpe_creep=False,
    bw_stable_2wk=False, strength_bounce=False, subjective_ok=False,
) -> EngineStateInput:
    return EngineStateInput(
        current_phase=current_phase, bodyweight=bodyweight,
        cut_to_stab_target=cut_to_stab_target,
        cut_to_stab_tolerance=cut_to_stab_tolerance,
        rhr_down=rhr_down, sleep_ok=sleep_ok, no_rpe_creep=no_rpe_creep,
        bw_stable_2wk=bw_stable_2wk, strength_bounce=strength_bounce,
        subjective_ok=subjective_ok,
    )


def make_context(movements=None, engine_state=None) -> AnalysisContext:
    return AnalysisContext(
        movements=list(movements or []),
        engine_state=engine_state or make_engine_state(),
    )


def _delta_for(result: AnalysisResult, movement_id: int) -> MovementStateDelta:
    return next(d for d in result.movement_deltas if d.movement_id == movement_id)


# ---------------------------------------------------------------------------
# Task 2 — e1RM update (always-on)
# ---------------------------------------------------------------------------

def test_e1rm_from_single_working_set():
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
    ])
    result = analyze_session(make_context([mv]))
    delta = _delta_for(result, 1)
    # estimate_e1rm(100, 5, 8.0, ON_TARGET) — assert it's a positive float > the raw load
    assert delta.new_e1rm is not None
    assert delta.new_e1rm > 100.0


def test_e1rm_is_max_across_working_sets():
    # A heavier top set and a lighter back-off; the top set must win.
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=80.0,  actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=7.0),
    ])
    from ironlog.engine.e1rm import estimate_e1rm
    expected = max(
        estimate_e1rm(100.0, 5, 8.0, FeedbackTap.ON_TARGET),
        estimate_e1rm(80.0, 8, 7.0, FeedbackTap.ON_TARGET),
    )
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm == expected


def test_fatigued_late_set_does_not_drag_e1rm_down():
    # A strong top set then a fatigued set-3 with fewer reps; e1RM tracks the top.
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=200.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=200.0, actual_reps=2, feedback_tap=FeedbackTap.TOO_HARD, target_rpe=8.0),
    ])
    from ironlog.engine.e1rm import estimate_e1rm
    top = estimate_e1rm(200.0, 5, 8.0, FeedbackTap.ON_TARGET)
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm == top  # the 5-rep set, not the fatigued 2-rep set


def test_no_qualifying_sets_leaves_e1rm_untouched():
    # All warmups / missing data → no anchor → new_e1rm is None.
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=45.0, actual_reps=10, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0, is_warmup=True),
        make_logged_set(actual_load=None, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=100.0, actual_reps=None, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=None, target_rpe=8.0),       # no tap
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=None),  # no rpe
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_analysis.py 2>&1 | tail -8'`
Expected: collection error — `ModuleNotFoundError: No module named 'ironlog.engine.analysis'`.

- [ ] **Step 3: Create the module with dataclasses + e1RM-only analyze_session**

Create `ironlog/engine/analysis.py`:

```python
"""
analysis.py — pure-logic post-session analysis (the ANALYZE step, docs/06 §0).

Computes an AnalysisResult of proposed state deltas from a just-logged session
plus current state. PURE: no DB, no network, no LLM. The writes happen in
persistence/apply.py (the single write point), mirroring the validator's
"compute violations, caller applies" contract.

One shared anchor — the best working set by estimate_e1rm — drives BOTH the
e1RM value and the outcome classification (CEILING/MISS/NEITHER). Measurements
(e1RM, phase-gate eval) are objective-independent; prescription machinery
(ceiling/failed counters, tier step-down) is PROGRESS-gated. The hook never
writes current_load (generation's job) or current_phase (report-only).

See docs/superpowers/specs/2026-06-25-analysis-hook-design.md for the full spec.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..models.enums import FeedbackTap, Objective, Phase
from .e1rm import estimate_e1rm
from .progression import step_down_tier


@dataclass
class LoggedSet:
    """One logged working set paired with its prescription.
    target_rpe feeds e1RM; target_reps_low/high feed the outcome
    classification; feedback_tap feeds both."""
    actual_load: Optional[float] = None
    actual_reps: Optional[int] = None
    feedback_tap: Optional[FeedbackTap] = None
    is_warmup: bool = False
    target_rpe: Optional[float] = None
    target_reps_low: Optional[int] = None
    target_reps_high: Optional[int] = None


@dataclass
class MovementAnalysisInput:
    movement_id: int
    objective: Objective = Objective.MAINTAIN
    current_tier: int = 0
    increment_ladder_len: int = 1
    consecutive_ceiling_sessions: int = 0
    consecutive_failed_progressions: int = 0
    logged_sets: List[LoggedSet] = field(default_factory=list)


@dataclass
class EngineStateInput:
    current_phase: Phase = Phase.CUT
    bodyweight: Optional[float] = None
    cut_to_stab_target: float = 213.0
    cut_to_stab_tolerance: float = 2.0
    rhr_down: bool = False
    sleep_ok: bool = False
    no_rpe_creep: bool = False
    bw_stable_2wk: bool = False
    strength_bounce: bool = False
    subjective_ok: bool = False


@dataclass
class AnalysisContext:
    movements: List[MovementAnalysisInput] = field(default_factory=list)
    engine_state: EngineStateInput = field(default_factory=EngineStateInput)


@dataclass
class MovementStateDelta:
    movement_id: int
    new_e1rm: Optional[float] = None               # None = untouched (no qualifying sets)
    new_tier: Optional[int] = None                 # None = unchanged
    new_consecutive_ceiling: Optional[int] = None  # None = unchanged
    new_consecutive_failed: Optional[int] = None   # None = unchanged
    # No current_load field — the hook NEVER writes current_load (generation's job).


@dataclass
class AnalysisResult:
    movement_deltas: List[MovementStateDelta] = field(default_factory=list)
    phase_transition_available: Optional[Phase] = None  # report-only; applier never writes current_phase


def _best_e1rm_set(logged_sets: List[LoggedSet]) -> Optional[Tuple[LoggedSet, float]]:
    """The anchor set: the e1RM-qualifying working set with the max estimate.
    Returns (set, e1rm) or None if no set qualifies. A qualifying set is a
    non-warmup set with load, reps, feedback_tap, and target_rpe all present."""
    best: Optional[Tuple[LoggedSet, float]] = None
    for s in logged_sets:
        if s.is_warmup:
            continue
        if s.actual_load is None or s.actual_reps is None:
            continue
        if s.feedback_tap is None or s.target_rpe is None:
            continue
        e1rm = estimate_e1rm(s.actual_load, s.actual_reps, s.target_rpe, s.feedback_tap)
        if best is None or e1rm > best[1]:
            best = (s, e1rm)
    return best


def _analyze_movement(mv: MovementAnalysisInput) -> MovementStateDelta:
    delta = MovementStateDelta(movement_id=mv.movement_id)
    anchor = _best_e1rm_set(mv.logged_sets)
    if anchor is None:
        return delta  # no anchor → everything untouched (None)
    _anchor_set, anchor_e1rm = anchor
    delta.new_e1rm = anchor_e1rm   # measurement: always-on, objective-independent
    # Tasks 3 will add the outcome/gating/tier logic here, using `_anchor_set`.
    return delta


def analyze_session(ctx: AnalysisContext) -> AnalysisResult:
    """Compute proposed state deltas for a logged session. Pure; never writes."""
    deltas = [_analyze_movement(mv) for mv in ctx.movements]
    # Task 4 will add phase-gate evaluation here.
    return AnalysisResult(movement_deltas=deltas)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_analysis.py 2>&1 | tail -4'`
Expected: `4 passed`. Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'` → `76 passed`.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/analysis.py tests/test_analysis.py
git commit -m "feat(analysis): dataclasses + e1RM update (always-on)

Pure-core scaffold for the analysis hook: LoggedSet / MovementAnalysisInput /
EngineStateInput / AnalysisContext inputs; MovementStateDelta / AnalysisResult
outputs. _best_e1rm_set finds the shared anchor (best working set by
estimate_e1rm); analyze_session computes new_e1rm from it (objective-
independent measurement). No-qualifying-sets → new_e1rm=None (untouched).
Outcome/gating/tier (Task 3) and phase gates (Task 4) follow.

4 e1RM tests; full suite 76.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Outcome determination + objective gating + tier step-down

**Files:**
- Modify: `ironlog/engine/analysis.py` — extend `_analyze_movement` with the outcome + counter + tier logic
- Modify: `tests/test_analysis.py` — append outcome/gating/tier cases

**Interfaces:**
- Consumes: `_best_e1rm_set` and the anchor set from Task 2; `step_down_tier` from `.progression`.
- Produces: `_analyze_movement` now also sets `new_consecutive_ceiling`, `new_consecutive_failed`, `new_tier` (all PROGRESS-gated).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis.py`:

```python
# ---------------------------------------------------------------------------
# Task 3 — outcome determination, objective gating, tier step-down
# ---------------------------------------------------------------------------

def _progress_mv(**kw):
    """A PROGRESS movement with a 3-rung ladder, defaults overridable."""
    return make_movement_input(objective=Objective.PROGRESS, increment_ladder_len=3, **kw)


def test_ceiling_increments_and_resets_failed():
    mv = _progress_mv(consecutive_ceiling_sessions=1, consecutive_failed_progressions=2, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # reps>=high, not TOO_HARD
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling == 2   # 1 + 1
    assert delta.new_consecutive_failed == 0     # reset
    assert delta.new_tier is None                # ceiling does not step tier


def test_miss_via_low_reps_increments_failed_and_resets_ceiling():
    mv = _progress_mv(consecutive_ceiling_sessions=2, consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # reps<low
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_failed == 1     # 0 + 1
    assert delta.new_consecutive_ceiling == 0     # reset


def test_miss_via_too_hard_even_when_reps_in_range():
    mv = _progress_mv(consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=6, feedback_tap=FeedbackTap.TOO_HARD,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # in-range but TOO_HARD
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_failed == 1
    assert delta.new_consecutive_ceiling == 0


def test_neither_resets_both_counters():
    mv = _progress_mv(consecutive_ceiling_sessions=2, consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=6, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # in-range, on-target
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling == 0
    assert delta.new_consecutive_failed == 0


def test_neither_when_anchor_lacks_rep_range():
    # No rep range and not TOO_HARD → can't classify hit/miss → NEITHER (both reset).
    mv = _progress_mv(consecutive_ceiling_sessions=2, consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=6, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling == 0
    assert delta.new_consecutive_failed == 0


def test_mutual_exclusivity_never_ticks_both():
    # Whatever the outcome, a ceiling increment and a failed increment never co-occur.
    for tap, reps in [(FeedbackTap.ON_TARGET, 8), (FeedbackTap.ON_TARGET, 3), (FeedbackTap.TOO_HARD, 6)]:
        mv = _progress_mv(consecutive_ceiling_sessions=5, consecutive_failed_progressions=5, logged_sets=[
            make_logged_set(actual_load=100.0, actual_reps=reps, feedback_tap=tap,
                            target_rpe=8.0, target_reps_low=5, target_reps_high=8),
        ])
        delta = _delta_for(analyze_session(make_context([mv])), 1)
        incremented = sum([
            delta.new_consecutive_ceiling == 6,
            delta.new_consecutive_failed == 6,
        ])
        assert incremented <= 1, f"both ticked for tap={tap} reps={reps}"


def test_maintain_leaves_counters_and_tier_untouched_but_updates_e1rm():
    mv = make_movement_input(objective=Objective.MAINTAIN, consecutive_ceiling_sessions=1,
                             consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # would be CEILING if PROGRESS
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling is None   # untouched in MAINTAIN
    assert delta.new_consecutive_failed is None
    assert delta.new_tier is None
    assert delta.new_e1rm is not None              # measurement still updates


def test_second_consecutive_miss_steps_tier_down_and_resets_failed():
    mv = _progress_mv(current_tier=0, increment_ladder_len=3,
                      consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # MISS → failed 1→2
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_tier == 1                    # step_down_tier(0, 3, 2) == 1
    assert delta.new_consecutive_failed == 0       # reset after the drop


def test_first_miss_does_not_step_tier():
    mv = _progress_mv(current_tier=0, increment_ladder_len=3,
                      consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # MISS → failed 0→1
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_tier is None                 # step_down_tier(0, 3, 1) == 0 (unchanged)
    assert delta.new_consecutive_failed == 1


def test_tier_step_down_respects_ladder_bound_at_finest_rung():
    # Already at the finest rung (tier == ladder_len - 1) → no drop even at threshold.
    mv = _progress_mv(current_tier=2, increment_ladder_len=3,
                      consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # MISS → failed 1→2
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_tier is None                 # step_down_tier(2, 3, 2) == 2 (unchanged) → None
    assert delta.new_consecutive_failed == 2       # counter still ticks; no drop available


def test_combined_too_hard_anchor_updates_e1rm_and_misses():
    # The decoupling proof: anchor set is TOO_HARD → e1RM still records AND outcome is MISS.
    mv = _progress_mv(consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=200.0, actual_reps=4, feedback_tap=FeedbackTap.TOO_HARD,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm is not None             # measurement records from the same anchor
    assert delta.new_consecutive_failed == 1       # prescription: MISS
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_analysis.py -k "ceiling or miss or neither or mutual or maintain or tier or combined" 2>&1 | tail -12'`
Expected: failures — `_analyze_movement` currently leaves all counters None, so the ceiling/miss/tier assertions fail. (The MAINTAIN test's e1rm assertion already passes; its counter assertions also pass since counters are None today — but the PROGRESS tests fail, which is the RED signal.)

- [ ] **Step 3: Extend `_analyze_movement`**

In `ironlog/engine/analysis.py`, replace the `_analyze_movement` function with:

```python
def _analyze_movement(mv: MovementAnalysisInput) -> MovementStateDelta:
    delta = MovementStateDelta(movement_id=mv.movement_id)
    anchor = _best_e1rm_set(mv.logged_sets)
    if anchor is None:
        return delta  # no anchor → everything untouched (None)
    anchor_set, anchor_e1rm = anchor
    delta.new_e1rm = anchor_e1rm   # measurement: always-on, objective-independent

    # Prescription machinery is PROGRESS-gated.
    if mv.objective != Objective.PROGRESS:
        return delta

    # Three-way outcome on the anchor set (mutually exclusive).
    is_too_hard = anchor_set.feedback_tap == FeedbackTap.TOO_HARD
    has_range = (anchor_set.target_reps_low is not None
                 and anchor_set.target_reps_high is not None)
    reps = anchor_set.actual_reps  # not None (anchor is a working set)

    if is_too_hard or (has_range and reps < anchor_set.target_reps_low):
        outcome = "MISS"
    elif has_range and reps >= anchor_set.target_reps_high:
        outcome = "CEILING"
    else:
        outcome = "NEITHER"

    if outcome == "CEILING":
        delta.new_consecutive_ceiling = mv.consecutive_ceiling_sessions + 1
        delta.new_consecutive_failed = 0
    elif outcome == "MISS":
        delta.new_consecutive_ceiling = 0
        new_failed = mv.consecutive_failed_progressions + 1
        stepped = step_down_tier(mv.current_tier, mv.increment_ladder_len,
                                 consecutive_fails=new_failed, threshold=2)
        if stepped != mv.current_tier:
            delta.new_tier = stepped
            delta.new_consecutive_failed = 0   # streak consumed by the drop
        else:
            delta.new_consecutive_failed = new_failed
    else:  # NEITHER
        delta.new_consecutive_ceiling = 0
        delta.new_consecutive_failed = 0

    return delta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_analysis.py 2>&1 | tail -4'`
Expected: `15 passed` (4 from Task 2 + 11 new). Full suite: `87 passed`.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/analysis.py tests/test_analysis.py
git commit -m "feat(analysis): outcome determination + objective gating + tier step-down

Three-way mutually-exclusive outcome on the shared anchor set: MISS
(TOO_HARD or reps<low), CEILING (reps>=high and not TOO_HARD), NEITHER
(else, incl. anchor lacking a rep range). All counters + tier are
PROGRESS-gated; MAINTAIN updates only e1RM. Tier step-down via the
existing step_down_tier helper after the 2nd consecutive miss, resetting
the failed streak on the drop and respecting the ladder bound.

11 new tests incl. mutual-exclusivity, MAINTAIN-gating, and the
TOO_HARD-anchor decoupling proof. Full suite 87.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Phase-gate evaluation + engine re-exports

**Files:**
- Modify: `ironlog/engine/analysis.py` — add phase-gate evaluation to `analyze_session`
- Modify: `ironlog/engine/__init__.py` — re-export the public surface
- Modify: `tests/test_analysis.py` — append phase-gate + re-export cases

**Interfaces:**
- Consumes: `EngineStateInput`, `Phase`.
- Produces: `analyze_session` now sets `AnalysisResult.phase_transition_available`. Public names re-exported from `ironlog.engine`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis.py`:

```python
# ---------------------------------------------------------------------------
# Task 4 — phase-gate evaluation + re-exports
# ---------------------------------------------------------------------------

def test_cut_to_stab_gate_met():
    es = make_engine_state(current_phase=Phase.CUT, bodyweight=214.0,
                           cut_to_stab_target=213.0, cut_to_stab_tolerance=2.0)  # 214 <= 215
    result = analyze_session(make_context([], es))
    assert result.phase_transition_available == Phase.STAB


def test_cut_to_stab_gate_not_met_when_too_heavy():
    es = make_engine_state(current_phase=Phase.CUT, bodyweight=220.0,
                           cut_to_stab_target=213.0, cut_to_stab_tolerance=2.0)  # 220 > 215
    result = analyze_session(make_context([], es))
    assert result.phase_transition_available is None


def test_cut_to_stab_gate_none_bodyweight_is_not_satisfied():
    es = make_engine_state(current_phase=Phase.CUT, bodyweight=None)
    result = analyze_session(make_context([], es))
    assert result.phase_transition_available is None  # missing data is "unavailable", never "met"


def test_stab_to_rebuild_gate_all_six_true():
    es = make_engine_state(current_phase=Phase.STAB, rhr_down=True, sleep_ok=True,
                           no_rpe_creep=True, bw_stable_2wk=True, strength_bounce=True,
                           subjective_ok=True)
    result = analyze_session(make_context([], es))
    assert result.phase_transition_available == Phase.REBUILD


def test_stab_to_rebuild_gate_one_false_blocks():
    es = make_engine_state(current_phase=Phase.STAB, rhr_down=True, sleep_ok=True,
                           no_rpe_creep=True, bw_stable_2wk=True, strength_bounce=True,
                           subjective_ok=False)  # one flag false
    result = analyze_session(make_context([], es))
    assert result.phase_transition_available is None


def test_no_gate_in_calibration_or_rebuild_phase():
    for ph in (Phase.CALIBRATION, Phase.REBUILD):
        es = make_engine_state(current_phase=ph, bodyweight=100.0, rhr_down=True, sleep_ok=True,
                               no_rpe_creep=True, bw_stable_2wk=True, strength_bounce=True,
                               subjective_ok=True)
        result = analyze_session(make_context([], es))
        assert result.phase_transition_available is None


def test_engine_package_reexports_analysis_api():
    from ironlog.engine import (
        analyze_session as eng_fn, AnalysisContext as eng_ctx,
        AnalysisResult as eng_res, MovementAnalysisInput as eng_mv,
        LoggedSet as eng_ls, EngineStateInput as eng_es,
        MovementStateDelta as eng_delta,
    )
    from ironlog.engine.analysis import (
        analyze_session, AnalysisContext, AnalysisResult, MovementAnalysisInput,
        LoggedSet, EngineStateInput, MovementStateDelta,
    )
    assert eng_fn is analyze_session
    assert eng_ctx is AnalysisContext
    assert eng_res is AnalysisResult
    assert eng_mv is MovementAnalysisInput
    assert eng_ls is LoggedSet
    assert eng_es is EngineStateInput
    assert eng_delta is MovementStateDelta
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_analysis.py -k "gate or rebuild or calibration or reexports" 2>&1 | tail -10'`
Expected: phase-gate tests fail (`phase_transition_available` is always None today); the re-export test fails on `ImportError`.

- [ ] **Step 3: Add phase-gate evaluation + a helper**

In `ironlog/engine/analysis.py`, add this helper above `analyze_session`:

```python
def _evaluate_phase_gate(es: EngineStateInput) -> Optional[Phase]:
    """Report (never apply) an available phase transition. None = no gate met."""
    if es.current_phase == Phase.CUT:
        if es.bodyweight is not None and es.bodyweight <= es.cut_to_stab_target + es.cut_to_stab_tolerance:
            return Phase.STAB
        return None
    if es.current_phase == Phase.STAB:
        if (es.rhr_down and es.sleep_ok and es.no_rpe_creep
                and es.bw_stable_2wk and es.strength_bounce and es.subjective_ok):
            return Phase.REBUILD
        return None
    return None
```

Then update `analyze_session` to populate the field:

```python
def analyze_session(ctx: AnalysisContext) -> AnalysisResult:
    """Compute proposed state deltas for a logged session. Pure; never writes."""
    deltas = [_analyze_movement(mv) for mv in ctx.movements]
    phase = _evaluate_phase_gate(ctx.engine_state)
    return AnalysisResult(movement_deltas=deltas, phase_transition_available=phase)
```

- [ ] **Step 4: Add the engine re-exports**

Edit `ironlog/engine/__init__.py`. After the existing `ledger` re-export line (`from .ledger import compute_tallies  # noqa: F401`), add:

```python
from .analysis import (                                            # noqa: F401
    AnalysisContext, AnalysisResult, EngineStateInput, LoggedSet,
    MovementAnalysisInput, MovementStateDelta, analyze_session,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_analysis.py 2>&1 | tail -4'`
Expected: `22 passed` (15 + 7 new). Full suite: `94 passed`.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/analysis.py ironlog/engine/__init__.py tests/test_analysis.py
git commit -m "feat(analysis): phase-gate evaluation (report-only) + engine re-exports

CUT->STAB on bodyweight <= target+tol (None bodyweight is NOT satisfied);
STAB->REBUILD on all six readiness flags; any other phase → None.
Report-only: analyze_session sets phase_transition_available; the applier
never writes current_phase. Public surface re-exported from ironlog.engine.

7 new tests; full suite 94. Pure core complete — applier is Task 5.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The applier (persistence/apply.py) + DB-touching tests

**Files:**
- Create: `ironlog/persistence/__init__.py` — empty package marker
- Create: `ironlog/persistence/apply.py` — `apply_analysis(result, db)`
- Create: `tests/test_apply_analysis.py` — 6 applier cases with in-memory SQLite

**Interfaces:**
- Consumes: `AnalysisResult`, `MovementStateDelta` from `..engine.analysis`; `MovementState` from `..models.library`.
- Produces: `apply_analysis(result: AnalysisResult, db: Session) -> None` — the single write point.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_apply_analysis.py`:

```python
"""Tests for ironlog.persistence.apply.apply_analysis (v0.4).

The first DB-touching test file. Uses an in-memory SQLite session; the
engine tests (tests/test_analysis.py) stay pure.
"""
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.library import EngineState, MovementState
from ironlog.models.enums import Phase
from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
from ironlog.persistence.apply import apply_analysis


@pytest.fixture
def db():
    engine = create_engine("sqlite://")  # in-memory
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_state(db, movement_id, **kw):
    state = MovementState(movement_id=movement_id, **kw)
    db.add(state)
    db.commit()
    return state


def test_each_field_written_when_delta_present(db):
    _seed_state(db, 1, e1rm=200.0, current_increment_tier=0,
                consecutive_ceiling_sessions=1, consecutive_failed_progressions=1)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=210.0, new_tier=1,
                           new_consecutive_ceiling=2, new_consecutive_failed=0),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 210.0
    assert s.current_increment_tier == 1
    assert s.consecutive_ceiling_sessions == 2
    assert s.consecutive_failed_progressions == 0


def test_none_fields_leave_columns_untouched(db):
    _seed_state(db, 1, e1rm=200.0, current_increment_tier=2,
                consecutive_ceiling_sessions=3, consecutive_failed_progressions=4)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=None, new_tier=None,
                           new_consecutive_ceiling=None, new_consecutive_failed=None),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 200.0                          # untouched
    assert s.current_increment_tier == 2
    assert s.consecutive_ceiling_sessions == 3
    assert s.consecutive_failed_progressions == 4


def test_e1rm_write_sets_updated_at(db):
    _seed_state(db, 1, e1rm=None, e1rm_updated_at=None)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=180.0),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 180.0
    assert s.e1rm_updated_at is not None             # timestamp written alongside e1rm


def test_existing_e1rm_preserved_when_delta_e1rm_is_none(db):
    _seed_state(db, 1, e1rm=275.0)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=None, new_consecutive_ceiling=1),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 275.0                           # not nulled
    assert s.consecutive_ceiling_sessions == 1


def test_phase_transition_available_does_not_write_current_phase(db):
    es = EngineState(id=1, current_phase=Phase.STAB)
    db.add(es); db.commit()
    _seed_state(db, 1, e1rm=200.0)
    result = AnalysisResult(
        movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=210.0)],
        phase_transition_available=Phase.REBUILD,
    )
    apply_analysis(result, db)
    es_after = db.exec(select(EngineState).where(EngineState.id == 1)).one()
    assert es_after.current_phase == Phase.STAB      # report-only: NOT flipped to REBUILD


def test_atomicity_missing_row_writes_nothing(db):
    _seed_state(db, 1, e1rm=200.0, consecutive_ceiling_sessions=0)
    # movement 2 has no MovementState row → .one() raises during resolve, pre-mutation.
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=999.0, new_consecutive_ceiling=9),
        MovementStateDelta(movement_id=2, new_e1rm=500.0),
    ])
    with pytest.raises(Exception):
        apply_analysis(result, db)
    db.rollback()  # clear the failed transaction before re-querying
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 200.0                           # movement 1 unchanged — no partial write
    assert s.consecutive_ceiling_sessions == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_apply_analysis.py 2>&1 | tail -8'`
Expected: collection error — `ModuleNotFoundError: No module named 'ironlog.persistence'`.

- [ ] **Step 3: Create the persistence package + applier**

Create `ironlog/persistence/__init__.py` (empty):

```python
```

Create `ironlog/persistence/apply.py`:

```python
"""
apply.py — the single write point for analysis results.

Reads an AnalysisResult (computed by the pure engine.analysis) and writes the
proposed MovementState deltas to the DB. This is the ONLY place analysis output
becomes persistent state, mirroring the validator's "engine computes, caller
applies" contract for writes.

Atomic by construction: all MovementState rows are resolved FIRST, so a missing
row raises before any mutation — no partial write. Never writes current_phase
(phase_transition_available is report-only). The v0.5 e1RM-history append hooks
into the new_e1rm branch in one line.
"""

from datetime import datetime, timezone

from sqlmodel import Session, select

from ..engine.analysis import AnalysisResult
from ..models.library import MovementState


def apply_analysis(result: AnalysisResult, db: Session) -> None:
    """Apply an AnalysisResult's MovementState deltas. The single write point."""
    # Resolve every row first — a missing row raises here, before any mutation.
    states = {
        d.movement_id: db.exec(
            select(MovementState).where(MovementState.movement_id == d.movement_id)
        ).one()
        for d in result.movement_deltas
    }
    now = datetime.now(timezone.utc)
    for d in result.movement_deltas:
        state = states[d.movement_id]
        if d.new_e1rm is not None:
            state.e1rm = d.new_e1rm
            state.e1rm_updated_at = now
            # v0.5 SEAM: append (d.movement_id, d.new_e1rm, now) to the e1RM history here.
        if d.new_tier is not None:
            state.current_increment_tier = d.new_tier
        if d.new_consecutive_ceiling is not None:
            state.consecutive_ceiling_sessions = d.new_consecutive_ceiling
        if d.new_consecutive_failed is not None:
            state.consecutive_failed_progressions = d.new_consecutive_failed
        db.add(state)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_apply_analysis.py 2>&1 | tail -4'`
Expected: `6 passed`. Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'` → `100 passed`.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/persistence/ tests/test_apply_analysis.py
git commit -m "feat(persistence): apply_analysis — the single analysis write point

New persistence/ package (first DB-writing code outside api/). apply_analysis
resolves all MovementState rows FIRST (a missing row raises before any
mutation — atomic, no partial write), then writes each non-None delta field
under one consistent timestamp. Never writes current_phase
(phase_transition_available is report-only). Carries the one-line v0.5
e1RM-history seam.

6 applier tests (first DB-touching test file, in-memory SQLite) incl. the
report-only-phase guarantee and the atomicity-on-missing-row check. Full
suite 100.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (against the spec)

**Spec coverage** — every spec section maps to a task:
- §3 Architecture (pure core + applier, persistence/ package) → Tasks 2-4 (core), Task 5 (applier).
- §4.1 Input dataclasses → Task 2 Step 3.
- §4.2 Output dataclasses → Task 2 Step 3.
- §5 Schema field → Task 1.
- §6.1 Single shared anchor (`_best_e1rm_set`) → Task 2 Step 3.
- §6.2 Three-way outcome → Task 3 Step 3 + tests (ceiling/miss-low/miss-toohard/neither/neither-no-range/mutual-exclusivity).
- §6.3 Counters + tier + objective gating → Task 3 Step 3 + the MAINTAIN-gating + tier tests.
- §6.4 Phase-gate evaluation (report-only, bodyweight-None-not-met) → Task 4 Step 3 + tests.
- §6.5 e1RM (always-on, best-set max, no-sets→untouched) → Task 2 Step 3 + tests.
- §7 Applier (resolve-all-first atomicity, single now(), v0.5 seam, never current_phase) → Task 5 Step 3 + tests.
- §8.1 Pure-core tests (~17) → Tasks 2-4 (4 + 11 + 7 = 22 analysis tests; exceeds the ~17 estimate because phase-gate + re-export got split into discrete cases — more granular, not over-built).
- §8.2 Applier tests (~6) → Task 5 (6 cases incl. atomicity + report-only-phase).
- §9 Build/verify → each task ends with a pytest run; final 100.
- §10 Wire impact none → enforced by absence (no api/ edits, no DTO changes).
- §11 Out of scope → enforced by absence (no history table, no auto-flip, no current_load write, no HTTP endpoint, no ledger coupling).
- §12 Invariants → Global Constraints block + per-task structure.

**Placeholder scan** — no TBDs, no "implement appropriate," no "fill in," no "similar to Task N" without code. Every code-changing step shows complete code. (Task 2's `_analyze_movement` has a comment "Tasks 3 will add..." — that is intentional staging in a real, runnable function, not a placeholder; Task 3 Step 3 replaces the whole function with complete code.)

**Type consistency** — `analyze_session(ctx: AnalysisContext) -> AnalysisResult` identical across Tasks 2/4 and re-export. `MovementStateDelta` field names (`new_e1rm`, `new_tier`, `new_consecutive_ceiling`, `new_consecutive_failed`) consistent across the dataclass (Task 2), the producer (`_analyze_movement`, Task 3), and the applier (Task 5). `_best_e1rm_set` signature stable Task 2→3. `step_down_tier(tier, ladder_len, consecutive_fails, threshold)` matches the existing `engine/progression.py` signature exactly (verified: `tier + 1` = finer rung, unchanged at `tier == ladder_len - 1`). `EngineStateInput` field names match between the dataclass, the helper, and the test factory. Final test count: 72 baseline + 22 analysis + 6 applier = 100.
