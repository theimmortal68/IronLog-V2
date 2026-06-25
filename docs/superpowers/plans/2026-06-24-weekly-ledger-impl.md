# WeeklyLedger (v0.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ironlog/engine/ledger.py` — a pure-logic aggregator that turns logged `SetLog` rows into a `WeeklyTallies` (the dataclass the v0.2 validator already consumes). Adds one new enum (`KneeModality`) + one nullable `Movement` column (`knee_modality`); no HTTP, no migration script, no persistence layer.

**Architecture:** Single new module `ironlog/engine/ledger.py` with one public function `compute_tallies(set_logs, movements) -> WeeklyTallies`. Two module-level frozensets carry the pull/push classification (`_HORIZONTAL_PULL` = `{ROW}`; `_HORIZONTAL_PUSH` = `{BENCH, CG_PRESS}`); knee modality is a real `Movement.knee_modality` column. Tests in `tests/test_ledger.py` (~13 cases) build SetLogs and Movements in-memory via factory helpers (same pattern as `tests/test_validator.py`).

**Tech Stack:** Python 3.14, SQLModel domain types (read-only — the ledger never queries), `dataclasses`, `enum.Enum`, pytest 8. No new dependencies. Re-uses `validator.WeeklyTallies` as the output type.

## Global Constraints

Carried verbatim from the approved spec and `~/projects/IronLog-V2/CLAUDE.md`. Every task's requirements implicitly include this section.

- **`engine/` is pure logic.** No DB / network / LLM / file I/O imports in `ledger.py`. All inputs arrive via function arguments; the caller does the DB query and date filtering.
- **Do NOT add `from __future__ import annotations`** to any file that imports SQLModel models with `Relationship(...)`. `ledger.py` imports `Movement` and `SetLog`, both of which have Relationships. Stringified annotations break SQLAlchemy resolution.
- **Re-use the validator's `WeeklyTallies`** as the output type. No parallel "LedgerSnapshot" type.
- **Pinned unit (pull/push volume):** `actual_load × actual_reps` summed per qualifying SetLog. Documented in the module docstring; pinned by `test_pull_volume_is_load_times_reps` and `test_push_volume_is_load_times_reps`.
- **Pinned unit (knee counts):** distinct sessions ("frequency"). A session with three working Nordic sets contributes `1` to `knee_counts["NORDIC"]`, not `3`. Pinned by `test_knee_counts_are_distinct_sessions`.
- **Pinned behavior (non-pull/push contributes to neither):** Back Squat / Hip Thrust / Lateral Raise / etc. produce zero pull and zero push volume. Pinned by `test_squat_contributes_to_neither_volume`.
- **Pinned behavior (zero-load contributes zero volume):** `actual_load=0, actual_reps=10` on a ROW set adds 0 to `pull_volume` (the set qualifies as working per §5.3, but `0 × 10 == 0`). Pinned by `test_zero_load_contributes_zero_volume`.
- **Silent-skip directions:** warmups skipped; `actual_load is None` or `actual_reps is None` skipped; movements absent from the `movements` dict skipped. The missing-movement skip undercounts — for frequency rules this is the safer error direction.
- **The ledger does NOT supply targets.** Returned `WeeklyTallies.knee_targets == {}` and `pull_push_target == 2.0` (the dataclass defaults). Whoever calls `validate()` merges targets in from PhasePolicy / EngineState / hardcoded constants.
- **Test runner is myflix via SSH.** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q [args]'` — workstation pytest imports fail; the venv on myflix is the working environment. Files NFS-sync workstation→myflix instantly.
- **Baseline: 57 tests pass** (post-v0.2.x cleanup at `fb1a1ba`). After v0.3 lands, full suite is 57 + 13 = ~70 tests, all green.

---

## File structure

Created or modified across this plan. Paths relative to `~/projects/IronLog-V2`.

```
ironlog/models/enums.py           # MODIFY in Task 1 — add KneeModality enum
ironlog/models/library.py         # MODIFY in Task 1 — add knee_modality field + import
ironlog/engine/ledger.py          # NEW in Task 2 — the pure-logic aggregator
ironlog/engine/__init__.py        # MODIFY in Task 3 — add compute_tallies + KneeModality re-exports
tests/test_ledger.py              # NEW in Task 2 — 13 test cases + factory helpers
```

No new HTTP routes. No migration script (existing prod DB on myflix gets a one-shot ALTER per spec §12, handled at deploy time after this plan lands). No client impact.

---

### Task 1: Schema additions — KneeModality enum + Movement.knee_modality field

**Files:**
- Modify: `ironlog/models/enums.py` — add `KneeModality(str, Enum)` near the other enums
- Modify: `ironlog/models/library.py` — add `KneeModality` to the imports from `.enums`, add `knee_modality: Optional[KneeModality] = None` field to `Movement`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `KneeModality(str, Enum)` with members `NORDIC, TIB, KOT, SISSY` (string values match enum names).
  - `Movement.knee_modality: Optional[KneeModality]` — nullable column, defaults to None. Re-exported automatically via `models/__init__.py`'s `from .enums import *`.

**Verification approach:** the existing 57-test suite imports `Movement` (transitively via `tests/test_validator.py` and the engine modules). If the new field has a syntax error or breaks SQLModel's table generation, all 57 tests fail on import. Running pytest is the schema sanity check; no dedicated schema test needed for v0.3.

- [ ] **Step 1: Confirm baseline is green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: `57 passed in <1s` (plus ~34 deprecation warnings from pydantic that are not our code).

- [ ] **Step 2: Add the KneeModality enum**

Edit `ironlog/models/enums.py`. Find the existing `BandCalStatus` enum and add the new enum right after it (or anywhere in the file — the order in `enums.py` doesn't matter functionally):

```python
class KneeModality(str, Enum):
    """Knee-modality classification for cross-session frequency tracking.

    Movements tagged with one of these contribute toward weekly knee-frequency
    targets (Nordic 2×/wk, tib 2×/wk, KOT 2×/wk, sissy 1×/wk per spec §4).
    Most movements have knee_modality == None (not a knee-prioritized lift).
    """
    NORDIC = "NORDIC"   # Nordic hamstring curls
    TIB = "TIB"         # tibialis anterior raises
    KOT = "KOT"         # knees-over-toes (ATG split squat, sissy progressions)
    SISSY = "SISSY"     # sissy squats
```

- [ ] **Step 3: Add the Movement.knee_modality field**

Edit `ironlog/models/library.py`. First, add `KneeModality` to the imports from `.enums`. Find the existing import block:

```python
from .enums import (
    AssistSubtype, AssistUnit, BandCalStatus, CalibrationStatus, EquipPhase,
    LiftCategory, LoadUnit, Objective, Phase, ProgressionMode, Region, Scheme,
    Status,
)
```

Change to (insert `KneeModality` alphabetically):

```python
from .enums import (
    AssistSubtype, AssistUnit, BandCalStatus, CalibrationStatus, EquipPhase,
    KneeModality, LiftCategory, LoadUnit, Objective, Phase, ProgressionMode,
    Region, Scheme, Status,
)
```

Then add the new field to the `Movement` class. Find this line in the class body:

```python
    status: Status = Status.ACTIVE
```

Add the new field immediately after it:

```python
    status: Status = Status.ACTIVE
    knee_modality: Optional[KneeModality] = None       # cross-session knee-frequency classification (v0.3)
```

- [ ] **Step 4: Run the full suite — confirm no regressions**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: `57 passed in <1s`. (If the new field breaks anything, every test fails on import; if any test fails, fix the field declaration before continuing.)

- [ ] **Step 5: Spot-check the field is reachable**

Run:
```
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -c "
from ironlog.models.library import Movement
from ironlog.models.enums import KneeModality
m = Movement(name=\"test\", base_name=\"test\", knee_modality=KneeModality.NORDIC)
print(m.knee_modality, type(m.knee_modality).__name__)
m2 = Movement(name=\"test2\", base_name=\"test2\")
assert m2.knee_modality is None
print(\"default OK\")"'
```
Expected output:
```
KneeModality.NORDIC KneeModality
default OK
```

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/models/enums.py ironlog/models/library.py
git commit -m "feat(model): KneeModality enum + Movement.knee_modality field (v0.3)

NORDIC / TIB / KOT / SISSY. Nullable column on Movement; defaults to None.
No existing seed data needs the field set (none of the 5 seeded movements
is a knee-modality lift). Re-exported via models/__init__.py's
\`from .enums import *\`.

Prerequisite for v0.3 WeeklyLedger (ironlog/engine/ledger.py, next task).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Ledger module + 13 tests (TDD)

**Files:**
- Create: `ironlog/engine/ledger.py` — the pure-logic aggregator
- Create: `tests/test_ledger.py` — 13 test cases + factory helpers

**Interfaces:**
- Consumes:
  - `KneeModality` from `..models.enums` (Task 1).
  - `Movement.knee_modality: Optional[KneeModality]` (Task 1).
  - `LiftCategory` from `..models.enums` (existing).
  - `Movement` from `..models.library` (existing).
  - `SetLog` from `..models.session` (existing).
  - `WeeklyTallies` from `.validator` (v0.2, existing).
- Produces:
  - `compute_tallies(set_logs: Iterable[SetLog], movements: Dict[int, Movement]) -> WeeklyTallies` — the public function.
  - Module-level constants `_HORIZONTAL_PULL: frozenset[LiftCategory]` and `_HORIZONTAL_PUSH: frozenset[LiftCategory]` (private; document for v0.5 supersession).
  - Test-only factory helpers in `tests/test_ledger.py` (module-level functions, NOT pytest fixtures): `make_movement`, `make_setlog`. Used only by this test file.

- [ ] **Step 1: Write the 13 tests + factory helpers**

Create `tests/test_ledger.py`:

```python
"""Tests for ironlog.engine.ledger.compute_tallies (v0.3 WeeklyLedger).

The 4 pinned tests document the canonical units (load×reps for volume,
distinct sessions for knee counts), the squat-contributes-to-neither
classification check, and the zero-load under-count case. The remaining 9
cover edge cases, default preservation, and multi-session aggregation.
"""
from datetime import datetime, timezone

from ironlog.models.enums import KneeModality, LiftCategory
from ironlog.models.library import Movement
from ironlog.models.session import SetLog
from ironlog.engine.ledger import compute_tallies
from ironlog.engine.validator import WeeklyTallies


# ---------------------------------------------------------------------------
# Factory helpers — module-level functions, shared by every test in the file.
# Mirror the pattern in tests/test_validator.py: plain functions, not fixtures.
# Construct domain objects via constructor kwargs (no DB).
# ---------------------------------------------------------------------------

def make_movement(
    movement_id: int,
    *,
    name: str = "TestMove",
    lift_category: LiftCategory = LiftCategory.NONE,
    knee_modality: KneeModality | None = None,
) -> Movement:
    return Movement(
        id=movement_id,
        name=name,
        base_name=name,
        lift_category=lift_category,
        knee_modality=knee_modality,
    )


def make_setlog(
    *,
    session_id: int,
    movement_id: int,
    set_index: int = 0,
    actual_load: float | None = None,
    actual_reps: int | None = None,
    is_warmup: bool = False,
) -> SetLog:
    return SetLog(
        session_id=session_id,
        movement_id=movement_id,
        set_index=set_index,
        performed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        actual_load=actual_load,
        actual_reps=actual_reps,
        is_warmup=is_warmup,
    )


# ---------------------------------------------------------------------------
# Core (7 cases — including all four pin tests)
# ---------------------------------------------------------------------------

def test_empty_input_returns_default_tallies():
    """Baseline: no set_logs and no movements -> a default-valued WeeklyTallies."""
    result = compute_tallies([], {})
    assert result == WeeklyTallies()
    assert result.knee_counts == {}
    assert result.pull_volume == 0.0
    assert result.push_volume == 0.0
    # defaults from the dataclass — the ledger does NOT produce targets
    assert result.knee_targets == {}
    assert result.pull_push_target == 2.0


def test_pull_volume_is_load_times_reps():
    """PIN: pull volume metric is load * reps (volume-load/tonnage)."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 1000.0
    assert result.push_volume == 0.0


def test_push_volume_is_load_times_reps():
    """PIN: push volume metric is load * reps. Both BENCH and CG_PRESS count."""
    movements = {
        1: make_movement(1, lift_category=LiftCategory.BENCH),
        2: make_movement(2, lift_category=LiftCategory.CG_PRESS),
    }
    logs = [
        make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=10),
        make_setlog(session_id=1, movement_id=2, set_index=1, actual_load=50.0, actual_reps=8),
    ]
    result = compute_tallies(logs, movements)
    assert result.push_volume == 1400.0  # 100*10 + 50*8
    assert result.pull_volume == 0.0


def test_knee_counts_are_distinct_sessions():
    """PIN: knee counts are session-frequency, NOT set-count.

    Three Nordic sets in one session -> count of 1. Two more in a second
    session -> count of 2. This is the standard "Nx/wk" reading.
    """
    movements = {1: make_movement(1, knee_modality=KneeModality.NORDIC)}
    logs = [
        # 3 sets in session 1 -> still count of 1
        make_setlog(session_id=1, movement_id=1, set_index=0, actual_load=0.0, actual_reps=8),
        make_setlog(session_id=1, movement_id=1, set_index=1, actual_load=0.0, actual_reps=8),
        make_setlog(session_id=1, movement_id=1, set_index=2, actual_load=0.0, actual_reps=8),
    ]
    assert compute_tallies(logs, movements).knee_counts == {"NORDIC": 1}

    # Add a 4th in session 2 -> count of 2
    logs.append(make_setlog(session_id=2, movement_id=1, actual_load=0.0, actual_reps=8))
    assert compute_tallies(logs, movements).knee_counts == {"NORDIC": 2}


def test_squat_contributes_to_neither_volume():
    """PIN: a Back Squat set produces zero pull AND zero push volume.

    Movements not in _HORIZONTAL_PULL or _HORIZONTAL_PUSH contribute to
    neither volume. Hip Thrust likewise tested.
    """
    movements = {
        1: make_movement(1, lift_category=LiftCategory.BACK_SQUAT),
        2: make_movement(2, lift_category=LiftCategory.HIP_THRUST),
    }
    logs = [
        make_setlog(session_id=1, movement_id=1, actual_load=225.0, actual_reps=5),
        make_setlog(session_id=1, movement_id=2, set_index=1, actual_load=315.0, actual_reps=8),
    ]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0
    assert result.push_volume == 0.0


def test_zero_load_contributes_zero_volume():
    """PIN: actual_load=0 on a ROW set produces 0 volume (0 * reps == 0).

    The set still qualifies as working per §5.3 (load is non-null, reps
    are non-null, not a warmup), but `0 × 10 == 0`. Documents the
    bodyweight/banded under-count case from spec §5.1.
    """
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=0.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0


def test_multi_modality_knee_mix():
    """One Nordic, one TIB, two KOT sessions; SISSY unused.

    knee_counts should have exactly three keys (no zero-count entries).
    """
    movements = {
        1: make_movement(1, knee_modality=KneeModality.NORDIC),
        2: make_movement(2, knee_modality=KneeModality.TIB),
        3: make_movement(3, knee_modality=KneeModality.KOT),
        # no SISSY-tagged movement
    }
    logs = [
        make_setlog(session_id=1, movement_id=1, actual_load=0.0, actual_reps=8),
        make_setlog(session_id=2, movement_id=2, actual_load=10.0, actual_reps=12),
        make_setlog(session_id=3, movement_id=3, actual_load=0.0, actual_reps=10),
        make_setlog(session_id=4, movement_id=3, actual_load=0.0, actual_reps=10),
    ]
    result = compute_tallies(logs, movements)
    assert result.knee_counts == {"NORDIC": 1, "TIB": 1, "KOT": 2}
    assert "SISSY" not in result.knee_counts


# ---------------------------------------------------------------------------
# Edge cases (6 cases)
# ---------------------------------------------------------------------------

def test_warmup_skipped():
    """is_warmup=True sets contribute to nothing."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [
        make_setlog(session_id=1, movement_id=1, set_index=0, actual_load=100.0, actual_reps=10, is_warmup=False),
        make_setlog(session_id=1, movement_id=1, set_index=1, actual_load=45.0,  actual_reps=10, is_warmup=True),
    ]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 1000.0  # warmup's 45*10 excluded


def test_null_actual_load_skipped():
    """actual_load=None -> set is skipped (volume can't be computed)."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=None, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0


def test_null_actual_reps_skipped():
    """actual_reps=None -> set is skipped."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=None)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0


def test_missing_movement_silently_skipped():
    """A SetLog referencing a movement_id not in the dict is silently skipped.

    Documents the under-count direction (safer for frequency rules).
    Existing valid SetLog still aggregated correctly.
    """
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [
        make_setlog(session_id=1, movement_id=1,  actual_load=100.0, actual_reps=10),  # known
        make_setlog(session_id=1, movement_id=99, actual_load=200.0, actual_reps=10),  # unknown
    ]
    result = compute_tallies(logs, movements)
    # No exception raised; unknown movement contributes nothing; known still counts.
    assert result.pull_volume == 1000.0


def test_targets_left_at_defaults():
    """Ledger does NOT supply knee_targets or pull_push_target — caller's job.

    Returned WeeklyTallies has the dataclass defaults regardless of input.
    """
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW, knee_modality=KneeModality.NORDIC)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.knee_targets == {}
    assert result.pull_push_target == 2.0


def test_mixed_multi_session_aggregation():
    """Cross-cutting: 3 sessions with assorted lift categories aggregate correctly.

    Session 1: ROW 100x10 (pull 1000) + BENCH 100x10 (push 1000) + Nordic 0x10 (knee NORDIC count 1)
    Session 2: ROW 110x8 (pull 880)
    Session 3: CG_PRESS 60x6 (push 360) + Lateral Raise 20x12 (NEITHER) + Back Squat 225x5 (NEITHER)
                                                                                  + Nordic 0x10 (knee NORDIC count 2)
    Expected:
      pull_volume  = 1000 + 880 = 1880
      push_volume  = 1000 + 360 = 1360
      knee_counts  = {"NORDIC": 2}
    """
    movements = {
        1: make_movement(1, lift_category=LiftCategory.ROW),
        2: make_movement(2, lift_category=LiftCategory.BENCH),
        3: make_movement(3, knee_modality=KneeModality.NORDIC),
        4: make_movement(4, lift_category=LiftCategory.CG_PRESS),
        5: make_movement(5, lift_category=LiftCategory.NONE),       # Lateral Raise
        6: make_movement(6, lift_category=LiftCategory.BACK_SQUAT),
    }
    logs = [
        # session 1
        make_setlog(session_id=1, movement_id=1, set_index=0, actual_load=100.0, actual_reps=10),
        make_setlog(session_id=1, movement_id=2, set_index=1, actual_load=100.0, actual_reps=10),
        make_setlog(session_id=1, movement_id=3, set_index=2, actual_load=0.0,   actual_reps=10),
        # session 2
        make_setlog(session_id=2, movement_id=1, set_index=0, actual_load=110.0, actual_reps=8),
        # session 3
        make_setlog(session_id=3, movement_id=4, set_index=0, actual_load=60.0,  actual_reps=6),
        make_setlog(session_id=3, movement_id=5, set_index=1, actual_load=20.0,  actual_reps=12),
        make_setlog(session_id=3, movement_id=6, set_index=2, actual_load=225.0, actual_reps=5),
        make_setlog(session_id=3, movement_id=3, set_index=3, actual_load=0.0,   actual_reps=10),
    ]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 1880.0
    assert result.push_volume == 1360.0
    assert result.knee_counts == {"NORDIC": 2}
```

- [ ] **Step 2: Run the tests — confirm they fail on missing module**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_ledger.py 2>&1 | tail -10'`
Expected: collection error — `ModuleNotFoundError: No module named 'ironlog.engine.ledger'`. All 13 tests fail to even collect, which is the expected RED-phase state.

- [ ] **Step 3: Create the ledger module**

Create `ironlog/engine/ledger.py` with this complete content:

```python
"""
ledger.py — pure-logic aggregator that turns logged sets into a WeeklyTallies.

Implements docs/06 §5 for the two items the v0.2 validator consumes: knee
modality counts and horizontal pull/push volume. Items 3 (semi-anchor
frequency) and 4 (broader per-pattern volume) are deferred to v0.5 along
with the pattern taxonomy that's their prerequisite.

The ledger is a producer counterpart to the validator (which is a consumer
of WeeklyTallies). It re-uses validator.WeeklyTallies as its output type:
one shape, one contract. The validator's cross-session checks pair with
the ledger's projection without any glue.

Pinned units (canonical, in docstrings + tests):
  * Pull/push volume metric = load * reps per set, summed across qualifying
    sets in the window. The validator's PULL_PUSH_RATIO rule expects this
    "volume-load / tonnage" semantic. NOT sets-only, NOT reps-only.
  * Knee counts unit = distinct sessions ("frequency"), NOT sets. A session
    with three working Nordic sets contributes 1 to knee_counts["NORDIC"],
    not 3. This is the standard "Nx/wk" reading from spec §4.

Classification (the v0.3 choices):
  * Knee modality: read directly from Movement.knee_modality (the new
    nullable enum column added in v0.3).
  * Pull/push: derived from Movement.lift_category via two module-level
    frozensets. Coarse stand-in; v0.5's pattern taxonomy may supersede
    this without changing the public function signature.

Silent-skip directions (graceful degradation):
  * Warmups (is_warmup=True): never count.
  * Incomplete log (actual_load=None or actual_reps=None): no volume can
    be computed; skip.
  * Movement absent from movements dict: skip the set log entirely.
    UNDERCOUNT DIRECTION: for frequency rules (KNEE_FREQUENCY), missing
    movement -> undercount -> over-prescription, which is the safer error
    direction (more knee work than required is safe; less than required is
    not). For ratio rules (PULL_PUSH_RATIO), the direction is symmetric
    and depends on which side is missing — callers should ensure the
    movements dict is complete for the set_logs they pass.
  * Zero load (actual_load == 0): the set qualifies as working but
    contributes 0 to volume (0 * reps == 0). For bodyweight or banded
    pull/push movements (not in current seed; possible future libraries),
    this silently under-counts pull/push volume — acceptable in v0.3
    per spec §5.1; v0.5 may revisit with a bodyweight-load convention.

Not in scope (see docs/superpowers/specs/2026-06-24-weekly-ledger-design.md §10):
  * HTTP endpoint, persistent ledger table, semi-anchor frequency, broader
    pattern volume, targets sourcing, date math, apply-clamps helper.
"""

from typing import Dict, Iterable

from ..models.enums import LiftCategory
from ..models.library import Movement
from ..models.session import SetLog
from .validator import WeeklyTallies


# Coarse pull/push stand-in derived from existing lift_category.
# v0.5's pattern taxonomy may supersede this — when it does, swap the
# derivation here without changing compute_tallies's signature.
_HORIZONTAL_PULL: frozenset[LiftCategory] = frozenset({LiftCategory.ROW})
_HORIZONTAL_PUSH: frozenset[LiftCategory] = frozenset({
    LiftCategory.BENCH, LiftCategory.CG_PRESS,
})


def compute_tallies(
    set_logs: Iterable[SetLog],
    movements: Dict[int, Movement],
) -> WeeklyTallies:
    """Aggregate logged sets into a WeeklyTallies projection.

    Inputs:
      set_logs:  the logged sets to consider. Caller pre-filters by date
                 and by session status (typically COMPLETED only).
      movements: dict of movement_id -> Movement, for classification lookup.
                 Movements absent from the dict are silently skipped.

    Output: a WeeklyTallies with `knee_counts`, `pull_volume`, `push_volume`
    populated. `knee_targets` is `{}` and `pull_push_target` is `2.0` (the
    dataclass defaults) — the ledger does NOT supply targets; the caller
    merges them in from PhasePolicy / EngineState / constants when calling
    validate().
    """
    pull_volume: float = 0.0
    push_volume: float = 0.0
    knee_sessions: Dict[str, set] = {}  # modality name -> set of session_ids

    for log in set_logs:
        # Working-set gate: warmups excluded; both load and reps must be present.
        if log.is_warmup:
            continue
        if log.actual_load is None or log.actual_reps is None:
            continue
        movement = movements.get(log.movement_id)
        if movement is None:
            continue  # under-count direction documented in module docstring

        # Volume aggregation: load * reps, classified by lift_category.
        # (load == 0 contributes 0; the set still qualifies for knee-count purposes.)
        volume_contribution = log.actual_load * log.actual_reps
        if movement.lift_category in _HORIZONTAL_PULL:
            pull_volume += volume_contribution
        elif movement.lift_category in _HORIZONTAL_PUSH:
            push_volume += volume_contribution
        # else: contributes to neither (squat, hip thrust, etc.)

        # Knee-count aggregation: distinct sessions per modality.
        if movement.knee_modality is not None:
            modality_key = movement.knee_modality.value
            knee_sessions.setdefault(modality_key, set()).add(log.session_id)

    knee_counts: Dict[str, int] = {k: len(v) for k, v in knee_sessions.items()}

    return WeeklyTallies(
        knee_counts=knee_counts,
        pull_volume=pull_volume,
        push_volume=push_volume,
        # knee_targets and pull_push_target left at WeeklyTallies dataclass defaults.
    )
```

- [ ] **Step 4: Run the tests — confirm all 13 pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_ledger.py 2>&1 | tail -5'`
Expected: `13 passed in <1s`.

Run the full suite as well: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: `70 passed in <1s` (57 baseline + 13 ledger). No regressions in any prior test.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/ledger.py tests/test_ledger.py
git commit -m "feat(ledger): WeeklyLedger aggregator — knee counts + pull/push volume

ironlog/engine/ledger.py: compute_tallies(set_logs, movements) -> WeeklyTallies.
Pure function; walks logged SetLogs, classifies via Movement.knee_modality
(NORDIC/TIB/KOT/SISSY) and Movement.lift_category (ROW -> pull;
BENCH/CG_PRESS -> push). Re-uses validator.WeeklyTallies as the output
type — same dataclass the v0.2 validator already consumes.

Pinned units:
  * pull/push volume = sum(actual_load * actual_reps) per qualifying set
  * knee counts = distinct sessions (\"frequency\"); 3 Nordic sets in 1
    session = 1 count, not 3
  * non-pull/push movements (squat, hip thrust, lateral raise) contribute
    to neither volume
  * actual_load=0 yields 0 volume contribution (set still qualifies for
    knee counts); spec §5.1 acceptable under-count for bodyweight movements

Silent-skip directions documented: warmups, null load/reps, missing
movements all excluded. Missing-movement undercount is the safer error
direction for frequency rules (over-prescription beats under-prescription
for knee work).

13 tests covering 4 pin tests + 6 edge cases + 1 empty + 1 multi-modality
+ 1 cross-cutting multi-session. Full suite: 70 passing (57 baseline + 13).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: engine/__init__ re-exports + final verification

**Files:**
- Modify: `ironlog/engine/__init__.py` — add ledger + KneeModality re-exports

**Interfaces:**
- Consumes: `compute_tallies` from `.ledger`, `KneeModality` from `..models.enums`.
- Produces: `compute_tallies` and `KneeModality` reachable from `ironlog.engine` directly (callers don't need to know the module structure).

**Why re-export KneeModality here too:** the validator's `WeeklyTallies` is already re-exported from `ironlog.engine` (Task 7 of v0.2). Callers building tallies will need `KneeModality` to populate `knee_counts` keys correctly, OR to tag Movements before passing them to the ledger. Pulling `KneeModality` up to the engine package keeps the import story consistent with the rest of the engine surface.

- [ ] **Step 1: Edit `ironlog/engine/__init__.py`**

Replace the existing file content with:

```python
from .e1rm import estimate_e1rm, epley_e1rm, implied_rir           # noqa: F401
from .loading import round_to_achievable, clamp_to_cap, current_increment  # noqa: F401
from .autoregulate import next_set_load                            # noqa: F401
from .progression import (                                          # noqa: F401
    resolve_objective, should_attempt_progression, step_down_tier,
    reset_tier_on_rebuild, maybe_reset_tier_on_breakthrough,
)
from .validator import (                                            # noqa: F401
    MovementInfo, RuleCode, ValidationContext, ValidationResult,
    Violation, ViolationKind, WeeklyTallies, validate,
)
from .ledger import compute_tallies                                 # noqa: F401
from ..models.enums import KneeModality                             # noqa: F401
```

- [ ] **Step 2: Add a re-export sanity test**

Append to `tests/test_ledger.py`:

```python
def test_engine_package_reexports_ledger_api():
    """compute_tallies and KneeModality are reachable from ironlog.engine
    via identity. Pins the re-export surface (catches future redefinition
    drift the same way Task 7 of v0.2 pinned the validator re-exports)."""
    from ironlog.engine import compute_tallies as eng_compute, KneeModality as eng_km
    from ironlog.engine.ledger import compute_tallies as ledger_compute
    from ironlog.models.enums import KneeModality as models_km
    assert eng_compute is ledger_compute
    assert eng_km is models_km
```

- [ ] **Step 3: Run the full suite — confirm 71 passing**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: `71 passed in <1s` (57 baseline + 13 ledger + 1 re-export sanity = 71).

- [ ] **Step 4: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/__init__.py tests/test_ledger.py
git commit -m "feat(engine): re-export compute_tallies + KneeModality from engine package

Matches the validator re-export pattern (Task 7 of v0.2). Callers can
\`from ironlog.engine import compute_tallies, KneeModality, WeeklyTallies\`
in one line. Re-export sanity test pinned via identity comparison; full
suite at 71 passing (57 baseline + 13 ledger + 1 re-export sanity).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (against the spec)

**Spec coverage** — every spec section maps to a task:
- §1 Purpose / §2 Constraints → implicit (Global Constraints block carries them through)
- §3 Architecture (single file, pure function, frozensets) → Task 2
- §4.1 KneeModality enum → Task 1 Step 2
- §4.2 Movement.knee_modality field → Task 1 Step 3
- §4.3 ledger.py module + compute_tallies signature → Task 2 Step 3
- §5.1 Pull/push volume = load×reps + zero-load behavior → Task 2 Step 3 (impl) + Step 1 tests `test_pull_volume_is_load_times_reps`, `test_push_volume_is_load_times_reps`, `test_zero_load_contributes_zero_volume`
- §5.2 Knee counts = distinct sessions → Task 2 Step 3 + test `test_knee_counts_are_distinct_sessions`
- §5.3 Working-set definition (is_warmup, null gates) → Task 2 Step 3 + tests `test_warmup_skipped`, `test_null_actual_load_skipped`, `test_null_actual_reps_skipped`
- §5.4 Classification (frozensets + knee_modality) → Task 2 Step 3 + test `test_squat_contributes_to_neither_volume`
- §6.1 Silent-skip table → Task 2 Step 3 (impl) + test `test_missing_movement_silently_skipped`
- §6.2 Under-count direction comment → Task 2 Step 3 (in module docstring)
- §6.3 What the ledger does NOT do → enforced by absence (no Session imports, no PlannedSet imports, no date math)
- §7 Testing strategy (~13 cases) → Task 2 Step 1 (13 tests) + Task 3 Step 2 (1 re-export test) = 14 total
- §8 Build & verify → all three tasks end with `pytest -q`
- §9 Wire impact: none → enforced by absence (no `ironlog/api/` edits, no DTO changes)
- §10 Out of scope → enforced by absence (no HTTP endpoint, no persistent table, no semi-anchor, no apply-clamps)
- §11 Architecture invariants → Global Constraints block + structural choices in each task
- §12 Deploy notes → out of scope for the implementation plan (deploy happens after merge, not as part of the build)

**Placeholder scan** — no TBDs, no "TODO", no "implement appropriate," no "fill in," no "similar to Task N" without code. Every code-changing step has a complete code block.

**Type consistency** — `compute_tallies(set_logs, movements)` signature is identical across Task 2 Step 1 (test imports), Task 2 Step 3 (impl), and Task 3 Step 2 (re-export sanity). `KneeModality` enum members (NORDIC/TIB/KOT/SISSY) consistent across Task 1 enum definition, Task 2's `make_movement` helper, all 13 tests, and the impl's `movement.knee_modality.value` lookup. `_HORIZONTAL_PULL` and `_HORIZONTAL_PUSH` frozensets defined once in Task 2 Step 3; values match the spec (`{ROW}` and `{BENCH, CG_PRESS}` respectively). WeeklyTallies field names (`knee_counts`, `pull_volume`, `push_volume`, `knee_targets`, `pull_push_target`) consistent throughout.
