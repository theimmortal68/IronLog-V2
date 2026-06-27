# v0.5 — e1RM History + Analyzers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the per-session e1RM history record and its two deterministic readers — calibration-flip (`CALIBRATING→MEASURED` on two agreeing weekly estimates) and stall detection (e1RM flat/declining over a PROGRESS window) — wired through a `run_analysis` orchestrator seam, with migration `003` for the history table.

**Architecture:** Pure engine functions (`calibration.py`, `stall.py`) + an extended single-write-point applier (`apply.py`) + a `run_analysis` orchestrator in `persistence/`. The history row is appended by the applier alongside `MovementState.e1rm`; `run_analysis` owns resolving/stamping the per-session objective+phase and buckets weeks via a caller-supplied `week_keyer`. All deterministic — generation (LLM) is v0.6.

**Tech Stack:** Python 3.14, SQLModel/SQLAlchemy, SQLite, pytest.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-06-26-v05-analyzers-design.md`). Every task implicitly includes these.

- **`engine/` is pure** — no DB/network/LLM/file-io/calendar math. `calibration.py` and `stall.py` receive pre-computed inputs.
- **`persistence/apply.py` is the single write point** — all `MovementState` writes + history appends + calibration flips happen there, one atomic resolve-all-first transaction. `run_analysis` orchestrates but writes nothing itself.
- **`current_load` has NO writer in v0.5** (generation owns it in v0.6). The applier must never set it. `detect_stall` writes nothing (pure recompute, no stored flag).
- **No `from __future__ import annotations`** (files import SQLModel `Relationship` models).
- **Migration rule:** single-statement-atomic OR fully idempotent (`IF NOT EXISTS`). `003` is one `CREATE TABLE IF NOT EXISTS`. `test_chain_matches_create_all` must stay green.
- **`calibration_status` PRE-EXISTS** on `MovementState` (`CalibrationStatus = INHERITED|CALIBRATING|MEASURED`). `003` adds ONLY the `e1rmhistory` table.
- **Named constants:** `CALIBRATION_AGREEMENT_PCT=0.05`; `STALL_WINDOW=3`, `STALL_MIN_SESSIONS=3`, `STALL_EPSILON_PCT=0.01`, `STALL_FAILED_THRESHOLD=2`.
- **Weekly aggregator = `max`** (decided). **Week boundary = `Callable[[date], WeekKey]`** parameter to `run_analysis` (decided); `WeekKey` is any hashable.
- **Tests run on myflix only:** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q [args]'` (venv lives there; workstation pytest fails ModuleNotFoundError). Edit locally; files NFS-sync instantly.
- **Baseline: 112 tests pass.**

---

## File structure

```
ironlog/models/library.py          MODIFY (Task 1) — add E1rmHistory model
deploy/migrations/003_add_e1rmhistory_table.sql   NEW (Task 1) — CREATE TABLE, parity-aligned
ironlog/engine/calibration.py      NEW (Task 2) — evaluate_calibration_flip (pure)
ironlog/engine/stall.py            NEW (Task 3) — detect_stall + StallSignal (pure)
ironlog/engine/analysis.py         MODIFY (Task 4) — delta carries anchor_load/reps/rpe + objective
ironlog/persistence/apply.py       MODIFY (Task 4) — append history row + write calibration flip
ironlog/persistence/run_analysis.py  NEW (Task 5) — the analyze→apply seam
ironlog/engine/__init__.py         MODIFY (Tasks 2,3) — re-exports
tests/test_calibration.py          NEW (Task 2)
tests/test_stall.py                NEW (Task 3)
tests/test_apply_analysis.py       MODIFY (Task 4) — history append + flip + current_load untouched
tests/test_run_analysis.py         NEW (Task 5) — bucketing/max/flip + mixed-objective window
tests/test_migrations.py           (Task 1 — parity test already exists; 003 joins the chain)
```

**Task dependency:** 1 (schema) → 4 (persistence) → 5 (seam). 2 and 3 (pure) are independent of everything and of each other. Suggested order: 1, 2, 3, 4, 5.

---

### Task 1: E1rmHistory model + migration 003 + parity (ITERATIVE single-green-gate unit)

Like the migrations-mechanism Task 2: write the model, draft `003`, run the parity test, align `003`'s DDL to what `create_all` emits until `test_chain_matches_create_all` is green. **Do not split** "write the model" and "write 003" — the parity test cannot pass until the DDL matches the model. Single green gate: parity green + full suite green.

**Files:**
- Modify: `ironlog/models/library.py` (add `E1rmHistory` near the other tables)
- Create: `deploy/migrations/003_add_e1rmhistory_table.sql`
- Test: `tests/test_migrations.py` (existing parity test exercises the new table; no new test code unless a gap appears)

**Interfaces:**
- Produces: `E1rmHistory` table `e1rmhistory` with columns `id, movement_id, session_id, e1rm, objective, phase, anchor_load, anchor_reps, anchor_rpe, computed_at`. Tasks 4/5 write rows; Task 5 reads them.

- [ ] **Step 1: Confirm baseline**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: `112 passed`.

- [ ] **Step 2: Add the `E1rmHistory` model**

In `ironlog/models/library.py`, add this class (place it after `MovementState`; `Objective` and `Phase` are already imported from `.enums`):

```python
class E1rmHistory(SQLModel, table=True):
    """Per-session anchor e1RM history (the append log behind MovementState.e1rm).

    One row per analyzed session per movement that had an anchor (a tapped
    working set). Readers: calibration-flip (weekly aggregation) and stall
    detection (PROGRESS-window trend). objective+phase are stamped per row so
    stall's window selection can filter to PROGRESS sessions without re-deriving.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    e1rm: float
    objective: Objective
    phase: Phase
    anchor_load: float
    anchor_reps: int
    anchor_rpe: float
    computed_at: datetime
```

- [ ] **Step 3: See what `create_all` emits for the new table (drives 003's DDL)**

Run on myflix (a fresh in-memory create_all, dump just this table's schema):
```
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -c "
import sqlite3, tempfile, os
from sqlmodel import SQLModel, create_engine
import ironlog.models  # noqa
p=tempfile.mktemp(suffix=\".db\"); e=create_engine(f\"sqlite:///{p}\"); SQLModel.metadata.create_all(e)
con=sqlite3.connect(p)
print([r[0] for r in con.execute(\"SELECT sql FROM sqlite_master WHERE name=\\\"e1rmhistory\\\"\")][0])
print(\"--- columns ---\")
for row in con.execute(\"SELECT name,type,\\\"notnull\\\",dflt_value,pk FROM pragma_table_info(\\\"e1rmhistory\\\")\"): print(row)
os.remove(p)
"'
```
Note the exact declared types (enums emit `VARCHAR(n)`; float→`FLOAT`; int→`INTEGER`; datetime→`DATETIME`). Use these verbatim in `003`.

- [ ] **Step 4: Write `003` aligned to that output**

Create `deploy/migrations/003_add_e1rmhistory_table.sql`. Draft (ALIGN every column's type to Step 3's output — the values below are the expected shapes; correct them if Step 3 differs):

```sql
CREATE TABLE IF NOT EXISTS e1rmhistory (
    id INTEGER NOT NULL PRIMARY KEY,
    movement_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    e1rm FLOAT NOT NULL,
    objective VARCHAR(8) NOT NULL,
    phase VARCHAR(11) NOT NULL,
    anchor_load FLOAT NOT NULL,
    anchor_reps INTEGER NOT NULL,
    anchor_rpe FLOAT NOT NULL,
    computed_at DATETIME NOT NULL,
    FOREIGN KEY(movement_id) REFERENCES movement (id),
    FOREIGN KEY(session_id) REFERENCES session (id)
);
CREATE INDEX IF NOT EXISTS ix_e1rmhistory_movement_id ON e1rmhistory (movement_id);
CREATE INDEX IF NOT EXISTS ix_e1rmhistory_session_id ON e1rmhistory (session_id);
```

Note: `create_all` emits indexes for the two `index=True` FKs (names like `ix_e1rmhistory_movement_id`) — include them in `003` so the chain matches. Confirm the exact index names/SQL from Step 3's `sqlite_master` (broaden the query to `WHERE tbl_name='e1rmhistory'` to see index DDL too). **The migration file may hold multiple `CREATE ... IF NOT EXISTS` statements — that's allowed under the authoring rule because every statement is idempotent.**

- [ ] **Step 5: Run the parity test — iterate DDL until green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_migrations.py::test_chain_matches_create_all 2>&1 | tail -30'`
The diff prints per-column / per-table mismatches. Align `003`'s column types, NOT NULL, defaults, PK, and the index DDL to `create_all`'s output until the map matches. Repeat until PASS. (This is the iteration loop; green = the `000+001+002+003` chain reconstructs live `create_all` exactly.)

- [ ] **Step 6: Full suite green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'`
Expected: `112 passed` (no new tests yet — the existing parity test now also covers `e1rmhistory`).

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/models/library.py deploy/migrations/003_add_e1rmhistory_table.sql
git commit -m "feat(v0.5): E1rmHistory table + migration 003 (parity green)

Per-session anchor e1RM history (table e1rmhistory) — the append log behind
MovementState.e1rm. Migration 003 adds only the table (calibration_status
pre-exists). DDL aligned to create_all; parity chain green.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `engine/calibration.py` — pure calibration-flip

**Files:**
- Create: `ironlog/engine/calibration.py`
- Modify: `ironlog/engine/__init__.py` (re-export)
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `CalibrationStatus` from `..models.enums`.
- Produces: `CALIBRATION_AGREEMENT_PCT: float`; `evaluate_calibration_flip(weekly_estimates: List[float], current_status: CalibrationStatus) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calibration.py`:

```python
"""Tests for engine.calibration — pure calibration-flip (CALIBRATING -> MEASURED)."""
from ironlog.engine.calibration import evaluate_calibration_flip
from ironlog.models.enums import CalibrationStatus

CALIBRATING = CalibrationStatus.CALIBRATING
INHERITED = CalibrationStatus.INHERITED
MEASURED = CalibrationStatus.MEASURED


def test_flip_when_last_two_within_5pct():
    # 200 vs 205 -> 5/205 = 2.4% <= 5% -> flip
    assert evaluate_calibration_flip([200.0, 205.0], CALIBRATING) is True


def test_no_flip_when_last_two_outside_5pct():
    # 200 vs 215 -> 15/215 = 7% > 5% -> no flip
    assert evaluate_calibration_flip([200.0, 215.0], CALIBRATING) is False


def test_thin_data_zero_estimates_no_flip():
    assert evaluate_calibration_flip([], CALIBRATING) is False


def test_thin_data_one_estimate_no_flip():
    assert evaluate_calibration_flip([200.0], CALIBRATING) is False


def test_one_way_no_flip_from_inherited():
    assert evaluate_calibration_flip([200.0, 201.0], INHERITED) is False


def test_one_way_no_flip_from_measured():
    assert evaluate_calibration_flip([200.0, 201.0], MEASURED) is False


def test_uses_last_two_not_any_two():
    # early pair agrees (200,201) but the LAST two (201, 230) disagree -> no flip
    assert evaluate_calibration_flip([200.0, 201.0, 230.0], CALIBRATING) is False
    # last two agree even though an earlier one is far off -> flip
    assert evaluate_calibration_flip([150.0, 200.0, 204.0], CALIBRATING) is True


def test_boundary_exactly_5pct_flips():
    # 200 vs 210 -> 10/210 = 4.76% <= 5% -> flip; 200 vs 210.6 -> 5.0% boundary
    assert evaluate_calibration_flip([200.0, 210.0], CALIBRATING) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_calibration.py 2>&1 | tail -6'`
Expected: collection error / `ModuleNotFoundError: No module named 'ironlog.engine.calibration'`.

- [ ] **Step 3: Implement `calibration.py`**

```python
"""
calibration.py — pure calibration-flip (docs/02; v0.5 spec §5).

A lift graduates CALIBRATING -> MEASURED when two consecutive weekly e1RM
estimates agree within CALIBRATION_AGREEMENT_PCT. PURE: receives pre-bucketed
weekly estimates (the caller aggregates session anchor e1RMs per week by max);
no rows, no dates, no calendar math. One-way: only fires from CALIBRATING.
The flip is fully reconstructable from the history rows + the WeekKeys that
defined the aggregation.
"""

from typing import List

from ..models.enums import CalibrationStatus

CALIBRATION_AGREEMENT_PCT = 0.05


def evaluate_calibration_flip(
    weekly_estimates: List[float],
    current_status: CalibrationStatus,
) -> bool:
    """True iff the lift should flip to MEASURED: currently CALIBRATING, at
    least two weekly estimates, and the LAST TWO agree within 5%.
    Thin data (0 or 1 estimates) -> False."""
    if current_status != CalibrationStatus.CALIBRATING:
        return False
    if len(weekly_estimates) < 2:
        return False
    a, b = weekly_estimates[-2], weekly_estimates[-1]
    denom = max(a, b)
    if denom <= 0:
        return False
    return abs(a - b) / denom <= CALIBRATION_AGREEMENT_PCT
```

- [ ] **Step 4: Re-export from `engine/__init__.py`**

Open `ironlog/engine/__init__.py` and add `evaluate_calibration_flip` (and `CALIBRATION_AGREEMENT_PCT` if the module re-exports constants) to the imports/`__all__`, matching the file's existing style.

- [ ] **Step 5: Run tests to green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_calibration.py 2>&1 | tail -4'`
Expected: `8 passed`. Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -1'` → `120 passed`.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/calibration.py ironlog/engine/__init__.py tests/test_calibration.py
git commit -m "feat(v0.5): pure calibration-flip (CALIBRATING->MEASURED, last-two within 5%)

Pure over pre-bucketed weekly estimates; one-way; thin-data-safe (0/1 -> no
flip). 8 tests. Suite 120.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `engine/stall.py` — pure stall detection (keystone matrix)

**Files:**
- Create: `ironlog/engine/stall.py`
- Modify: `ironlog/engine/__init__.py` (re-export)
- Test: `tests/test_stall.py`

**Interfaces:**
- Consumes: `Objective` from `..models.enums`.
- Produces: constants `STALL_WINDOW, STALL_MIN_SESSIONS, STALL_EPSILON_PCT, STALL_FAILED_THRESHOLD`; `@dataclass StallSignal(trend_stalled: bool, failed_stalled: bool, stalled: bool)`; `detect_stall(progress_anchor_e1rms: List[float], consecutive_failed: int, objective: Objective) -> StallSignal`.

- [ ] **Step 1: Write the failing tests (the explicit matrix)**

Create `tests/test_stall.py`:

```python
"""Tests for engine.stall — pure stall detection. The dip-and-recover case is
the keystone: it passes a naive monotonic test but fails on real noisy e1RM."""
from ironlog.engine.stall import detect_stall, StallSignal
from ironlog.models.enums import Objective

PROGRESS = Objective.PROGRESS
MAINTAIN = Objective.MAINTAIN


def test_dip_and_recover_not_trend_stalled():
    # 100 -> 95 -> 102: recovers above the window start by >1% -> NOT stalled (KEYSTONE)
    sig = detect_stall([100.0, 95.0, 102.0], 0, PROGRESS)
    assert sig.trend_stalled is False
    assert sig.stalled is False


def test_plateau_trend_stalled():
    # flat within epsilon -> stalled
    sig = detect_stall([100.0, 100.0, 100.5], 0, PROGRESS)
    assert sig.trend_stalled is True
    assert sig.stalled is True


def test_decline_trend_stalled():
    sig = detect_stall([100.0, 98.0, 96.0], 0, PROGRESS)
    assert sig.trend_stalled is True


def test_monotonic_climb_not_stalled():
    sig = detect_stall([100.0, 103.0, 106.0], 0, PROGRESS)
    assert sig.trend_stalled is False


def test_fewer_than_min_sessions_not_trend_stalled():
    assert detect_stall([100.0, 100.0], 0, PROGRESS).trend_stalled is False
    assert detect_stall([100.0], 0, PROGRESS).trend_stalled is False
    assert detect_stall([], 0, PROGRESS).trend_stalled is False


def test_failed_stalled_at_threshold():
    # climbing e1RM (not trend-stalled) but 2 failed prescriptions -> failed_stalled
    sig = detect_stall([100.0, 103.0, 106.0], 2, PROGRESS)
    assert sig.trend_stalled is False
    assert sig.failed_stalled is True
    assert sig.stalled is True


def test_failed_below_threshold_not_failed_stalled():
    sig = detect_stall([100.0, 103.0, 106.0], 1, PROGRESS)
    assert sig.failed_stalled is False


def test_stalled_is_union():
    # trend stalled OR failed stalled
    assert detect_stall([100.0, 100.0, 100.0], 0, PROGRESS).stalled is True   # trend only
    assert detect_stall([100.0, 103.0, 106.0], 2, PROGRESS).stalled is True   # failed only


def test_non_progress_objective_all_false():
    # a maintained lift is never stalled, even with flat e1RM + failures
    sig = detect_stall([100.0, 100.0, 100.0], 5, MAINTAIN)
    assert sig.trend_stalled is False
    assert sig.failed_stalled is False
    assert sig.stalled is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_stall.py 2>&1 | tail -6'`
Expected: `ModuleNotFoundError: No module named 'ironlog.engine.stall'`.

- [ ] **Step 3: Implement `stall.py`**

```python
"""
stall.py — pure stall detection (docs/06 §9/§183; v0.5 spec §6).

Two arms: an e1RM-trend arm over a PROGRESS window, and a failed-prescription
arm (the existing consecutive_failed counter). PURE: receives the lift's last
STALL_WINDOW PROGRESS-objective anchor e1RMs (the caller does the PROGRESS-window
selection) and the failed counter; returns both sub-signals plus their union.
No stored flag (ledger precedent — stall is a current-condition recompute).

trend_stalled uses a WHOLE-WINDOW definition: no e1RM in the window exceeds the
window's START by more than STALL_EPSILON_PCT. This catches plateau and decline
but NOT dip-and-recover (e.g. 100->95->102), which an endpoint comparison would
false-flag.
"""

from dataclasses import dataclass
from typing import List

from ..models.enums import Objective

STALL_WINDOW = 3
STALL_MIN_SESSIONS = 3
STALL_EPSILON_PCT = 0.01
STALL_FAILED_THRESHOLD = 2


@dataclass
class StallSignal:
    trend_stalled: bool
    failed_stalled: bool
    stalled: bool  # convenience: trend_stalled or failed_stalled


def detect_stall(
    progress_anchor_e1rms: List[float],
    consecutive_failed: int,
    objective: Objective,
) -> StallSignal:
    """Stall signal for a lift. progress_anchor_e1rms are the anchor e1RMs from
    the lift's last STALL_WINDOW PROGRESS sessions, oldest-first (the caller
    selects them). PROGRESS-gated: a non-PROGRESS lift is never stalled."""
    if objective != Objective.PROGRESS:
        return StallSignal(False, False, False)

    window = progress_anchor_e1rms[-STALL_WINDOW:]
    if len(window) >= STALL_MIN_SESSIONS:
        start = window[0]
        threshold = start * (1 + STALL_EPSILON_PCT)
        trend_stalled = max(window) <= threshold
    else:
        trend_stalled = False  # not enough data

    failed_stalled = consecutive_failed >= STALL_FAILED_THRESHOLD
    return StallSignal(trend_stalled, failed_stalled, trend_stalled or failed_stalled)
```

- [ ] **Step 4: Re-export from `engine/__init__.py`**

Add `detect_stall` and `StallSignal` to `ironlog/engine/__init__.py` imports/`__all__`, matching existing style.

- [ ] **Step 5: Run tests to green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_stall.py 2>&1 | tail -4'`
Expected: `9 passed`. Full suite → `129 passed`.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/stall.py ironlog/engine/__init__.py tests/test_stall.py
git commit -m "feat(v0.5): pure stall detection (whole-window trend + failed arm)

detect_stall returns trend_stalled/failed_stalled/stalled. Whole-window trend
def catches plateau+decline but spares dip-and-recover (the keystone case).
PROGRESS-gated; no stored flag. 9 tests. Suite 129.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: persistence — history append + calibration write (extend the applier)

**Files:**
- Modify: `ironlog/engine/analysis.py` (delta carries anchor details + objective)
- Modify: `ironlog/persistence/apply.py` (append history rows + write flip; never current_load)
- Test: `tests/test_apply_analysis.py` (extend)

**Interfaces:**
- Consumes: `E1rmHistory` (Task 1); `MovementStateDelta`, `AnalysisResult` (analysis.py); `CalibrationStatus` (enums).
- Produces: extended `MovementStateDelta` fields `objective: Optional[Objective]`, `anchor_load/anchor_reps/anchor_rpe: Optional[...]`; extended `apply_analysis(result, db, *, session_id=None, phase=None, calibration_flips=frozenset())`. Task 5 calls this signature.

- [ ] **Step 1: Extend `MovementStateDelta` + populate anchor details in analysis.py**

In `ironlog/engine/analysis.py`, add fields to `MovementStateDelta` (after `new_consecutive_failed`):

```python
    # v0.5: anchor details + objective for the e1RM-history row (None when no anchor)
    objective: Optional[Objective] = None
    anchor_load: Optional[float] = None
    anchor_reps: Optional[int] = None
    anchor_rpe: Optional[float] = None
```

In `_analyze_movement`, right after `delta.new_e1rm = anchor_e1rm` (where the anchor is known), populate them:

```python
    delta.new_e1rm = anchor_e1rm   # measurement: always-on, objective-independent
    delta.objective = mv.objective
    delta.anchor_load = anchor_set.actual_load
    delta.anchor_reps = anchor_set.actual_reps
    delta.anchor_rpe = anchor_set.target_rpe
```

(`Objective` is already imported in analysis.py.)

- [ ] **Step 2: Write the failing applier tests**

Add to `tests/test_apply_analysis.py` (it already has the in-memory-DB fixtures; mirror their pattern — create the `movement` + `movementstate` rows the deltas reference, and a `session` row for the FK). Add:

```python
def test_apply_appends_e1rm_history_row_when_session_and_phase_given(db_with_state):
    # db_with_state: a fixture with a movement(id=1), its movementstate, and a session(id=1)
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import Objective, Phase
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    delta = MovementStateDelta(
        movement_id=1, new_e1rm=205.0, objective=Objective.PROGRESS,
        anchor_load=180.0, anchor_reps=5, anchor_rpe=8.0,
    )
    apply_analysis(AnalysisResult(movement_deltas=[delta]), db_with_state,
                   session_id=1, phase=Phase.CUT)
    rows = db_with_state.exec(select(E1rmHistory)).all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.movement_id, r.session_id, r.e1rm) == (1, 1, 205.0)
    assert r.objective == Objective.PROGRESS and r.phase == Phase.CUT
    assert (r.anchor_load, r.anchor_reps, r.anchor_rpe) == (180.0, 5, 8.0)


def test_apply_no_history_row_when_no_anchor(db_with_state):
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import Phase
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    # new_e1rm None -> no anchor -> no history row
    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1)]),
                   db_with_state, session_id=1, phase=Phase.CUT)
    assert db_with_state.exec(select(E1rmHistory)).all() == []


def test_apply_writes_calibration_flip(db_with_state):
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import CalibrationStatus, Phase
    from ironlog.models.library import MovementState
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=205.0)]),
                   db_with_state, session_id=1, phase=Phase.CUT, calibration_flips=frozenset({1}))
    st = db_with_state.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert st.calibration_status == CalibrationStatus.MEASURED


def test_apply_never_writes_current_load(db_with_state):
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import Phase
    from ironlog.models.library import MovementState
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    before = db_with_state.exec(select(MovementState).where(MovementState.movement_id == 1)).one().current_load
    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=205.0)]),
                   db_with_state, session_id=1, phase=Phase.CUT, calibration_flips=frozenset({1}))
    after = db_with_state.exec(select(MovementState).where(MovementState.movement_id == 1)).one().current_load
    assert after == before  # current_load is untouched by the applier (two-writer boundary)


def test_apply_backward_compatible_without_new_kwargs(db_with_state):
    # existing call shape still works: no history append, no flip
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=205.0)]),
                   db_with_state)
    assert db_with_state.exec(select(E1rmHistory)).all() == []  # no session/phase -> no rows
```

Add a `db_with_state` fixture if the file lacks one (reuse the existing in-memory engine setup; seed `Movement(id=1, ...)`, `MovementState(movement_id=1, current_load=100.0, calibration_status=CALIBRATING)`, `Session(id=1, date=...)`). If the existing tests already have an equivalent fixture, extend it rather than duplicating.

- [ ] **Step 3: Run to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_apply_analysis.py 2>&1 | tail -8'`
Expected: failures — `apply_analysis() got an unexpected keyword argument 'session_id'` (and `E1rmHistory` import works from Task 1).

- [ ] **Step 4: Extend `apply.py`**

Rewrite `apply_analysis` in `ironlog/persistence/apply.py` to the extended signature (additive — defaults preserve the old behavior):

```python
from datetime import datetime, timezone
from typing import FrozenSet, Optional

from sqlmodel import Session, select

from ..engine.analysis import AnalysisResult
from ..models.enums import CalibrationStatus, Phase
from ..models.library import E1rmHistory, MovementState


def apply_analysis(
    result: AnalysisResult,
    db: Session,
    *,
    session_id: Optional[int] = None,
    phase: Optional[Phase] = None,
    calibration_flips: FrozenSet[int] = frozenset(),
) -> None:
    """Apply an AnalysisResult's MovementState deltas. The single write point.

    When session_id and phase are supplied (the run_analysis path), also append
    one E1rmHistory row per movement that has an anchor (new_e1rm is not None),
    stamped with objective/phase/anchor details. Flips calibration_status to
    MEASURED for any movement_id in calibration_flips. Never writes current_load.
    """
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
            if session_id is not None and phase is not None:
                db.add(E1rmHistory(
                    movement_id=d.movement_id,
                    session_id=session_id,
                    e1rm=d.new_e1rm,
                    objective=d.objective,
                    phase=phase,
                    anchor_load=d.anchor_load,
                    anchor_reps=d.anchor_reps,
                    anchor_rpe=d.anchor_rpe,
                    computed_at=now,
                ))
        if d.new_tier is not None:
            state.current_increment_tier = d.new_tier
        if d.new_consecutive_ceiling is not None:
            state.consecutive_ceiling_sessions = d.new_consecutive_ceiling
        if d.new_consecutive_failed is not None:
            state.consecutive_failed_progressions = d.new_consecutive_failed
        if d.movement_id in calibration_flips:
            state.calibration_status = CalibrationStatus.MEASURED
        db.add(state)
    db.commit()
```

(Note: `current_load` appears nowhere — the two-writer boundary holds by construction. The resolve-all-first dict keeps atomicity: a missing state row raises before any `db.add`.)

- [ ] **Step 5: Run tests to green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_apply_analysis.py 2>&1 | tail -4'`
Expected: all pass (existing + 5 new). Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -1'` → `134 passed`.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/analysis.py ironlog/persistence/apply.py tests/test_apply_analysis.py
git commit -m "feat(v0.5): applier appends e1RM history + writes calibration flip

MovementStateDelta carries anchor_load/reps/rpe + objective (populated when an
anchor exists). apply_analysis gains session_id/phase/calibration_flips kwargs:
appends one E1rmHistory row per anchored movement and flips calibration_status
to MEASURED. current_load never written (two-writer boundary). Backward
compatible without the new kwargs. Suite 134.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `persistence/run_analysis.py` — the analyze→apply seam

**Files:**
- Create: `ironlog/persistence/run_analysis.py`
- Test: `tests/test_run_analysis.py`

**Interfaces:**
- Consumes: `analyze_session`, `AnalysisContext`, `MovementAnalysisInput`, `EngineStateInput`, `LoggedSet` (analysis.py); `apply_analysis` (Task 4); `evaluate_calibration_flip` (Task 2); `E1rmHistory`, `MovementState`, `Movement`, `Session`, `SetLog` (models); `CalibrationStatus` (enums).
- Produces: `run_analysis(session_id: int, db: Session, week_keyer: Callable[[date], WeekKey]) -> AnalysisResult`. `WeekKey` is any hashable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_analysis.py`. Use an in-memory DB (mirror `test_apply_analysis.py`'s setup). Seed: a `Movement(id=1)` with `MovementState(movement_id=1, calibration_status=CALIBRATING, objective via...)`; enough `Session` + `SetLog` rows to drive an anchor; and prior `E1rmHistory` rows across two weeks for the flip test. A simple `week_keyer` for tests: `lambda d: (d.isocalendar().year, d.isocalendar().week)`.

```python
"""Tests for persistence.run_analysis — the analyze->apply seam."""
from datetime import date

from sqlmodel import select

from ironlog.models.enums import CalibrationStatus, Objective
from ironlog.models.library import E1rmHistory, MovementState
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])


def test_appends_history_row_for_analyzed_session(seeded_db):
    # seeded_db: movement(1)+state(CALIBRATING), session(1) with one tapped working set
    run_analysis(1, seeded_db, WEEK_KEYER)
    rows = seeded_db.exec(select(E1rmHistory).where(E1rmHistory.session_id == 1)).all()
    assert len(rows) == 1
    assert rows[0].objective == Objective.PROGRESS  # stamped by run_analysis


def test_calibration_flip_via_week_keyer_max_aggregation(seeded_db_two_weeks):
    # seeded_db_two_weeks: CALIBRATING lift with prior history rows in week A (max 200)
    # and the current session in week B producing ~204 -> two weekly maxes within 5% -> flip
    run_analysis(2, seeded_db_two_weeks, WEEK_KEYER)
    st = seeded_db_two_weeks.exec(
        select(MovementState).where(MovementState.movement_id == 1)).one()
    assert st.calibration_status == CalibrationStatus.MEASURED


def test_current_load_untouched(seeded_db):
    before = seeded_db.exec(select(MovementState).where(MovementState.movement_id == 1)).one().current_load
    run_analysis(1, seeded_db, WEEK_KEYER)
    after = seeded_db.exec(select(MovementState).where(MovementState.movement_id == 1)).one().current_load
    assert after == before


def test_mixed_objective_history_window_selects_only_progress(seeded_db_mixed):
    # History has interleaved MAINTAIN rows among PROGRESS rows. The PROGRESS-window
    # selection (the caller's job) must pick only the last STALL_WINDOW PROGRESS rows.
    # run_analysis exposes the selected progress e1RMs it would hand detect_stall;
    # assert the MAINTAIN rows are excluded from that selection.
    selected = run_analysis(3, seeded_db_mixed, WEEK_KEYER, _return_progress_window=True)
    # the helper returns the per-movement progress window it selected (test-only hook)
    assert all(obj == Objective.PROGRESS for obj in selected.objectives_used)
```

> Implementer note: the last test asserts window-selection. Since `detect_stall` is NOT called by `run_analysis` in v0.5 (it's a v0.6 consumer), expose the PROGRESS-window selection as a small **pure helper** in `run_analysis.py` — e.g. `select_progress_window(history_rows, window=STALL_WINDOW) -> list[float]` — and test THAT helper directly with mixed-objective rows, rather than threading a `_return_progress_window` flag through `run_analysis`. Rewrite this test to call the helper directly (cleaner than a test-only kwarg). The helper is what v0.6 will reuse to feed `detect_stall`.

- [ ] **Step 2: Run to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_run_analysis.py 2>&1 | tail -6'`
Expected: `ModuleNotFoundError: No module named 'ironlog.persistence.run_analysis'`.

- [ ] **Step 3: Implement `run_analysis.py`**

```python
"""
run_analysis.py — the deterministic analyze->apply seam (v0.5 spec §7).

Resolves context for a logged session, runs the pure analysis, buckets e1RM
history into weekly estimates via a CALLER-SUPPLIED week_keyer (no calendar math
here beyond applying the callable), evaluates calibration flips, and calls the
single-write-point applier once. Writes nothing itself.

No HTTP. v0.6 generation calls this seam. detect_stall is NOT called here (it's
a v0.6 consumer); select_progress_window is the pure helper v0.6 will use to
feed it. Cold-start is expected: until ~3 PROGRESS sessions log, the analyzers
are data-starved — this is correct, not broken.
"""

from collections import defaultdict
from datetime import date
from typing import Callable, Hashable, List

from sqlmodel import Session as DBSession
from sqlmodel import select

from ..engine.analysis import (
    AnalysisContext, AnalysisResult, EngineStateInput, LoggedSet,
    MovementAnalysisInput, analyze_session,
)
from ..engine.calibration import evaluate_calibration_flip
from ..engine.stall import STALL_WINDOW
from ..models.enums import CalibrationStatus, Objective
from ..models.library import E1rmHistory, EngineState, Movement, MovementState
from ..models.session import Session as WorkoutSession
from ..models.session import SetLog
from .apply import apply_analysis

WeekKey = Hashable


def select_progress_window(
    history_rows: List[E1rmHistory],
    window: int = STALL_WINDOW,
) -> List[float]:
    """The last `window` PROGRESS-objective anchor e1RMs, oldest-first.
    Window-selection is the caller's job (detect_stall takes pre-filtered e1RMs).
    Interleaved MAINTAIN/MEASURE rows are excluded. v0.6 feeds this to detect_stall."""
    progress = [r for r in history_rows if r.objective == Objective.PROGRESS]
    progress.sort(key=lambda r: r.computed_at)
    return [r.e1rm for r in progress[-window:]]


def _weekly_max_estimates(
    rows: List[E1rmHistory],
    session_date_by_id: dict,
    week_keyer: Callable[[date], WeekKey],
) -> List[float]:
    """Bucket history rows by week_keyer(session date), aggregate each week by
    max, return estimates ordered by week key (chronological)."""
    by_week = defaultdict(list)
    for r in rows:
        wk = week_keyer(session_date_by_id[r.session_id])
        by_week[wk].append(r.e1rm)
    return [max(by_week[wk]) for wk in sorted(by_week)]


def run_analysis(
    session_id: int,
    db: DBSession,
    week_keyer: Callable[[date], WeekKey],
) -> AnalysisResult:
    """Analyze one logged session and apply the results (single transaction)."""
    workout = db.exec(
        select(WorkoutSession).where(WorkoutSession.id == session_id)).one()
    phase = db.exec(select(EngineState)).one().current_phase

    set_logs = db.exec(select(SetLog).where(SetLog.session_id == session_id)).all()
    movement_ids = sorted({sl.movement_id for sl in set_logs})

    # Build per-movement analysis inputs from current state + this session's sets.
    movements_inputs = []
    state_by_mv = {}
    for mid in movement_ids:
        state = db.exec(
            select(MovementState).where(MovementState.movement_id == mid)).one()
        state_by_mv[mid] = state
        movement = db.exec(select(Movement).where(Movement.id == mid)).one()
        logged = [
            LoggedSet(
                actual_load=sl.actual_load, actual_reps=sl.actual_reps,
                feedback_tap=sl.feedback_tap, is_warmup=sl.is_warmup,
                target_rpe=sl.target_rpe,
                target_reps_low=sl.target_reps_low, target_reps_high=sl.target_reps_high,
            )
            for sl in set_logs if sl.movement_id == mid
        ]
        movements_inputs.append(MovementAnalysisInput(
            movement_id=mid,
            objective=movement.objective_override or Objective.MAINTAIN,
            current_tier=state.current_increment_tier,
            increment_ladder_len=len(movement.increment_ladder or [1]),
            consecutive_ceiling_sessions=state.consecutive_ceiling_sessions,
            consecutive_failed_progressions=state.consecutive_failed_progressions,
            logged_sets=logged,
        ))

    ctx = AnalysisContext(
        movements=movements_inputs,
        engine_state=EngineStateInput(current_phase=phase),
    )
    result = analyze_session(ctx)

    # Calibration flips: for each CALIBRATING lift, weekly-max estimates incl. this
    # session's just-computed e1RM (already in the delta; persisted by the applier).
    flips = set()
    session_date_by_id = {workout.id: workout.date}
    for d in result.movement_deltas:
        if d.new_e1rm is None:
            continue
        state = state_by_mv[d.movement_id]
        if state.calibration_status != CalibrationStatus.CALIBRATING:
            continue
        prior = db.exec(
            select(E1rmHistory).where(E1rmHistory.movement_id == d.movement_id)).all()
        for r in prior:
            if r.session_id not in session_date_by_id:
                session_date_by_id[r.session_id] = db.exec(
                    select(WorkoutSession).where(WorkoutSession.id == r.session_id)).one().date
        # include this session's new estimate as a synthetic current-week row
        weekly = _weekly_max_estimates(
            prior + [E1rmHistory(movement_id=d.movement_id, session_id=workout.id,
                                 e1rm=d.new_e1rm, objective=d.objective, phase=phase,
                                 anchor_load=d.anchor_load, anchor_reps=d.anchor_reps,
                                 anchor_rpe=d.anchor_rpe, computed_at=workout.generated_at)],
            session_date_by_id, week_keyer)
        if evaluate_calibration_flip(weekly, state.calibration_status):
            flips.add(d.movement_id)

    apply_analysis(result, db, session_id=session_id, phase=phase,
                   calibration_flips=frozenset(flips))
    return result
```

> Implementer notes: (1) Verify the real field names against the models — `Movement.objective_override`, `Movement.increment_ladder`, `EngineState.current_phase`, `WorkoutSession.date`/`.generated_at`, `SetLog.target_reps_low/high/target_rpe/feedback_tap/is_warmup`. Adjust to the actual columns (grep them); the logic is fixed, the field names must match reality. (2) The synthetic current-week row mirrors what the applier will persist — it's used only to compute the flip in the same call; the applier writes the real row. Keep them consistent (same e1rm/date bucket). (3) `EngineState` is a single-row table — `.one()` is correct if exactly one row is seeded.

- [ ] **Step 4: Rewrite the mixed-objective test against the helper**

Replace `test_mixed_objective_history_window_selects_only_progress` with a direct unit test of `select_progress_window`:

```python
def test_select_progress_window_excludes_maintenance():
    from datetime import datetime, timezone
    from ironlog.engine.stall import STALL_WINDOW
    from ironlog.models.enums import Objective, Phase
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.run_analysis import select_progress_window

    def row(e, obj, t):
        return E1rmHistory(movement_id=1, session_id=t, e1rm=e, objective=obj,
                           phase=Phase.CUT, anchor_load=e*0.9, anchor_reps=5,
                           anchor_rpe=8.0, computed_at=datetime(2026, 1, t, tzinfo=timezone.utc))
    rows = [row(100, Objective.PROGRESS, 1), row(999, Objective.MAINTAIN, 2),
            row(102, Objective.PROGRESS, 3), row(104, Objective.PROGRESS, 4)]
    # last STALL_WINDOW PROGRESS rows, oldest-first; the MAINTAIN 999 is excluded
    assert select_progress_window(rows, STALL_WINDOW) == [100.0, 102.0, 104.0]
```

- [ ] **Step 5: Run tests to green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_run_analysis.py 2>&1 | tail -8'`
Iterate on field-name mismatches (Step 3 note 1) until green. Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'` → expected ~`138 passed` (4 run_analysis tests; adjust count to actual).

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/persistence/run_analysis.py tests/test_run_analysis.py
git commit -m "feat(v0.5): run_analysis seam (resolve -> analyze -> weekly-max flip -> apply)

The deterministic analyze->apply boundary (no HTTP). Resolves context, runs
analyze_session, buckets e1RM history by the caller-supplied week_keyer with max
aggregation, evaluates calibration flips, calls the applier once. Owns per-session
objective/phase stamping. select_progress_window is the pure PROGRESS-window
helper v0.6 will feed to detect_stall. current_load untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (against the spec)

**Spec coverage:** §4 history record → Task 1 (model) + Task 4 (append). §5 calibration-flip → Task 2 (pure) + Task 5 (weekly-max bucketing via week_keyer + flip eval). §6 stall → Task 3 (pure) + Task 5 (`select_progress_window` PROGRESS-window helper). §7 run_analysis seam → Task 5. §8 two-writer boundary → Task 4 (`current_load` untouched test) + applier code (no `current_load` reference). §9 testing → all the named cases incl. dip-and-recover (Task 3), thin-data (Task 2), mixed-objective window (Task 5). Migration 003 + parity → Task 1. Constants → Tasks 2/3. Gate decisions: `max` aggregator (Task 5 `_weekly_max_estimates`), `Callable[[date], WeekKey]` (Task 5 signature).

**Placeholder scan:** No TBDs. Two implementer-judgment spots are explicit, not vague: Task 1 Step 3-5 (align 003 DDL to create_all's actual output — the iteration loop, same as migrations Task 2) and Task 5 Step 3 note 1 (verify model field names against reality — the logic is fixed, names must match). Both name exactly how to resolve.

**Type consistency:** `evaluate_calibration_flip(List[float], CalibrationStatus) -> bool`, `detect_stall(List[float], int, Objective) -> StallSignal`, `apply_analysis(result, db, *, session_id, phase, calibration_flips)`, `run_analysis(session_id, db, week_keyer)`, `select_progress_window(rows, window) -> List[float]` — consistent across the tasks that define and call them. `MovementStateDelta` new fields (`objective`, `anchor_load/reps/rpe`) defined in Task 4, consumed by the applier in the same task and produced by `run_analysis` in Task 5. Test counts: 112 → 120 (T2 +8) → 129 (T3 +9) → 134 (T4 +5) → ~138 (T5 +4); Task 1 adds 0 (parity test already counts).

**Keystone gates** (for the final review): (1) migration 003 parity green (Task 1); (2) dip-and-recover NOT trend_stalled (Task 3); (3) `current_load` never written by the applier (Task 4 + Task 5 tests); (4) calibration flip one-way + thin-data-safe + reconstructable via WeekKeys (Task 2 + Task 5).
