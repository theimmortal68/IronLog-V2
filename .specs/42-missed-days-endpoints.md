# Spec 42: GET /missed-days + acknowledge/reschedule endpoints

## Objective
Add the athlete-facing endpoints for reading current missed-day records and acting on them, per the design doc §Components 4.

## File targets
- New: `ironlog/api/schemas_missed_days.py` (response DTOs — mirror `ironlog/api/schemas_goals.py`'s exact convention/style: plain pydantic `BaseModel`, no extra decorators).
- Modify: `ironlog/api/app.py` — add three endpoints.
- New tests: `tests/test_missed_days_endpoint.py`.

## The fix

### `ironlog/api/schemas_missed_days.py`
```python
"""Missed-day records API contract."""
from datetime import date, datetime
from pydantic import BaseModel

class MissedDayRecordOut(BaseModel):
    id: int
    program_day_id: int
    day_role: str
    week_start_date: date
    detected_at: datetime
    status: str
```
`day_role` is not a field on `MissedDayRecord` itself (it lives on `ProgramDay`) — the endpoint joins to `ProgramDay` to include it in the response so the client doesn't need a second lookup.

### `ironlog/api/app.py` — three new endpoints
Mirror this file's established `db: Session = Depends(get_session)` pattern (see `get_goals`/`post_goals` for the closest analog — simple read + simple status-mutating writes, no complex upsert logic needed here since these are pure status transitions on an existing row, not a create-or-update).

```python
@app.get("/missed-days", response_model=List[MissedDayRecordOut])
def get_missed_days(db: Session = Depends(get_session)):
    """Returns current PENDING/RESCHEDULED MissedDayRecord rows
    (ACKNOWLEDGED/RESOLVED are settled, not surfaced)."""
    ...

@app.post("/missed-days/{record_id}/acknowledge", response_model=MissedDayRecordOut)
def acknowledge_missed_day(record_id: int, db: Session = Depends(get_session)):
    """Sets status=ACKNOWLEDGED. 404 if record_id doesn't exist."""
    ...

@app.post("/missed-days/{record_id}/reschedule", response_model=MissedDayRecordOut)
def reschedule_missed_day(record_id: int, db: Session = Depends(get_session)):
    """Sets status=RESCHEDULED. 404 if record_id doesn't exist."""
    ...
```

## Edge cases
- **`GET /missed-days` with no records at all** — returns `[]`, not an error.
- **`acknowledge`/`reschedule` on a nonexistent `record_id`** — `HTTPException(404, ...)`, not an unhandled crash or silent no-op.
- **`acknowledge`/`reschedule` on an already-`RESOLVED` record** — decide and document the behavior: either allow it (harmless status downgrade-then-upgrade churn, since the resolution pass will just flip it back to `RESOLVED` on its next run if a session genuinely exists) or reject it with a clear 400 ("already resolved"). Prefer allowing it (simpler, the nightly resolution pass is the source of truth and will self-correct) unless you find a concrete reason not to — document whichever choice you make in the endpoint's docstring.
- **`GET /missed-days` response includes `day_role`** — confirmed via a join to `ProgramDay`, not a second client-side lookup. If a `MissedDayRecord`'s `program_day_id` somehow doesn't resolve to an existing `ProgramDay` (shouldn't happen, `ProgramDay` rows are locked reference data, but the FK doesn't enforce it in SQLite by default), do not let this crash the whole list response — skip that record or use a placeholder, your choice, but don't let one bad row 500 the entire endpoint.
- Do NOT touch `check_missed_days` (`ironlog/persistence/missed_days.py`, a separate spec) — this spec is CRUD-only on the `status` field.

## Dependencies
Depends on spec 39 (`MissedDayRecord` model) merged first. Does NOT depend on spec 40 (the detection logic) — this endpoint can be written and tested against directly-seeded rows, same pattern as `GET /weak-points` not depending on its writer spec. Parallel-safe with spec 40 (no shared files: this spec touches `ironlog/api/app.py` + new `schemas_missed_days.py`; spec 40 touches `ironlog/persistence/missed_days.py`).

## Verification
- New tests: empty-list response before any records exist; a seeded `PENDING` record appears in `GET /missed-days` with the correct joined `day_role`; an `ACKNOWLEDGED`/`RESOLVED` record does NOT appear in `GET /missed-days`; `acknowledge`/`reschedule` correctly mutate status and are reflected in a subsequent `GET`; acting on a nonexistent `record_id` returns 404.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 624 passing, or higher if spec 39 already merged in this batch — check `main`'s actual count before dispatching).
