# Spec 40: Missed-day detection + auto-resolution logic

## Objective
Add the function that detects newly-missed training days (for the active program) and auto-resolves previously-missed ones once a matching session is logged, per the design doc §Components 1 and 3.

## File targets
- New: `ironlog/persistence/missed_days.py` (mirrors `ironlog/integrations/withings.py`'s shape — a single module combining the date-resolution logic and the DB read/write orchestration, rather than a strict pure-engine/impure-persistence split; this is a self-contained "sync job" style feature, same category as the Withings sync).
- New tests: `tests/test_missed_days_detection.py`.

## The fix

```python
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from ..models.library import EngineState
from ..models.program import MissedDayRecord, ProgramDay
from ..models.session import Session as WorkoutSession

GRACE_HOURS = 6  # a day isn't flagged missed until this many hours into the next day


def resolve_day_date(day_index: int, week_start: date) -> date:
    """day_index is 1=Mon...7=Sun. week_start must be a Monday. Returns
    the actual calendar date for that day_index within that week."""
    ...


def _current_week_start(as_of: date) -> date:
    """Monday of the week containing as_of."""
    ...


def check_missed_days(db: Session, as_of: Optional[datetime] = None) -> dict:
    """Detects newly-missed days for the active program's current week,
    and auto-resolves previously PENDING/RESCHEDULED records where a
    matching session now exists. as_of defaults to datetime.utcnow()
    if not given (a parameter, not a hardcoded call inside the
    function, so tests can pass a fixed time instead of depending on
    the real clock). Returns a summary dict, e.g.
    {"newly_missed": N, "resolved": N}."""
    ...
```

`check_missed_days`'s detection pass:
- Resolve the active program via `EngineState.active_program_id` (query `EngineState` singleton, `id=1`; if `active_program_id` is `None`, there's no active program — return a summary with zero counts, do not crash).
- Query all non-rest `ProgramDay` rows (`is_rest == False`) for that `program_id`.
- For each, compute its calendar date this week via `resolve_day_date(day_index, _current_week_start(as_of.date()))`.
- A day is eligible for missed-detection only if `as_of >= datetime.combine(that_date, time.min) + timedelta(days=1, hours=GRACE_HOURS)` — i.e. the day has fully elapsed AND the grace window (6 hours into the following day) has passed. Do the arithmetic explicitly and test the boundary (a day 5 hours into the next day is NOT yet eligible; 6 hours into the next day IS eligible — inclusive at the boundary, `>=`).
- For an eligible day: check whether a `WorkoutSession` exists with `date` within that calendar week and `day_role` matching this `ProgramDay.day_role`. If none exists AND no `MissedDayRecord` already exists for this exact `(program_day_id, week_start_date)` pair, insert a new `MissedDayRecord(program_day_id=..., week_start_date=..., status="PENDING")`.

`check_missed_days`'s resolution pass (same function call, runs after the detection pass, same `db.commit()`):
- Query all `MissedDayRecord` rows with `status in ("PENDING", "RESCHEDULED")`.
- For each, look up its `ProgramDay` (for `day_role`) and check whether a `WorkoutSession` now exists matching that `day_role` within that record's `week_start_date`'s week. If so, set `status="RESOLVED"`, `resolved_at=as_of`.

`db.commit()` once at the end of the function (mirror `sync_withings_measurements`'s single-commit-at-the-end shape, do not commit mid-function).

## Edge cases
- **No active program** (`EngineState.active_program_id is None`) — return `{"newly_missed": 0, "resolved": 0}`, no crash, no records touched.
- **A day within the grace window** (e.g. 3 hours into the next day) — NOT yet eligible for detection, no record created this run (will be caught on a later run once the grace window passes).
- **Running detection twice for the same already-missed day** — the second run must NOT create a duplicate `MissedDayRecord` for the same `(program_day_id, week_start_date)` — check for an existing record first.
- **A `MissedDayRecord` already `ACKNOWLEDGED`** — the resolution pass should still check it (an acknowledged-but-later-actually-trained day should still resolve) — only records already `RESOLVED` are skipped in the resolution pass (nothing left to do for those).
- **Multiple missed days across the week** — each `ProgramDay` is evaluated independently; a partial week (some days trained, some missed) produces exactly the missed ones as records, not a single record for the whole week.
- Do NOT touch `EngineState`, `ProgramDay`, `WorkoutSession`, or any other existing model — this spec only reads them and writes `MissedDayRecord`.

## Dependencies
Depends on spec 39 (`MissedDayRecord` model) merged first.

## Verification
- New tests for `resolve_day_date`: day_index=1 (Monday) → week_start itself; day_index=7 (Sunday) → week_start + 6 days.
- New tests for `check_missed_days`: (a) an eligible missed day with no session → creates a `PENDING` record; (b) the same scenario run twice → only one record exists, not two; (c) a day within the grace window → no record yet; (d) a `PENDING` record where a matching session now exists → flips to `RESOLVED` with `resolved_at` set; (e) an `ACKNOWLEDGED` record where a matching session now exists → also flips to `RESOLVED`; (f) no active program → zero-count summary, no crash; (g) a rest day (`is_rest=True`) is never flagged even if unattended.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 624 passing, or higher if spec 39 already merged in this batch — check `main`'s actual count before dispatching).
