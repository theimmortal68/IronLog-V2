# Spec 45: Cardio-log endpoints

## Objective
Add `POST /cardio-log`, `GET /cardio-log`, and `GET /cardio-log/weekly-summary` so the client can create, list, and roll up Z2 cardio sessions.

## File targets
- New: `ironlog/api/schemas_cardio_log.py`
- Modify: `ironlog/api/app.py`
- New: `tests/test_cardio_log_endpoints.py`

## The fix

`ironlog/api/schemas_cardio_log.py`:
```python
"""Cardio-log API contract."""
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel

class CardioLogCreate(BaseModel):
    date: date
    duration_minutes: int
    avg_hr: Optional[int] = None
    modality: str  # "WALK" | "TREADMILL"
    incline_pct: Optional[float] = None
    backward_walk_done: bool = False

class CardioLogOut(BaseModel):
    id: int
    date: date
    duration_minutes: int
    avg_hr: Optional[int]
    modality: str
    incline_pct: Optional[float]
    backward_walk_done: bool
    created_at: datetime

class CardioWeeklySummaryOut(BaseModel):
    count: int
    target: int
    week_start: date
```

`ironlog/api/app.py` — add three endpoints, mirroring the established pattern from `GET/POST /goals` and `GET /missed-days` (simple CRUD, no join complexity here since `CardioLog` has no foreign keys):

```python
@app.post("/cardio-log", response_model=CardioLogOut)
def create_cardio_log(payload: CardioLogCreate, db: Session = Depends(get_session)):
    log = CardioLog(**payload.model_dump())
    db.add(log)
    db.commit()
    db.refresh(log)
    return log

@app.get("/cardio-log", response_model=List[CardioLogOut])
def get_cardio_log(db: Session = Depends(get_session)):
    return db.exec(select(CardioLog).order_by(CardioLog.date.desc(), CardioLog.id.desc())).all()

@app.get("/cardio-log/weekly-summary", response_model=CardioWeeklySummaryOut)
def get_cardio_weekly_summary(db: Session = Depends(get_session)):
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    count = len(db.exec(select(CardioLog).where(CardioLog.date >= week_start)).all())
    return CardioWeeklySummaryOut(count=count, target=2, week_start=week_start)
```

Reuse the EXACT week-start formula already established in `ironlog/persistence/missed_days.py::_current_week_start` (`as_of - timedelta(days=as_of.weekday())`) — do not reimplement it differently. Import `timedelta`/`date` at the top of `app.py` if not already imported (check first, this file likely already imports `date` for other endpoints).

## Edge cases
- `weekly-summary`'s week window is `[week_start, today]` inclusive going forward — a log dated LATER than today should not occur in practice (client always sends today or a backfilled PAST date), but the query as written (`date >= week_start`) would still count a future-dated row if one existed; this is acceptable since the spec doesn't require future-date rejection (log-only, no validation beyond basic field types).
- `GET /cardio-log` returns ALL rows, most-recent-first — no pagination needed at this scale (a single-user app logging ~2x/week).
- `modality` is a plain string field, not a validated enum at the Pydantic layer — matches this repo's existing convention for movement-adjacent string fields (e.g. `TierExercise.tier_role`) rather than introducing a new enum class for a two-value field. Do not add enum validation beyond what's specified here.

## Dependencies
Depends on spec 44 (`CardioLog` model) merged first.

## Verification
- `POST /cardio-log` creates and returns a row with all fields correctly persisted, including nullable ones.
- `GET /cardio-log` returns rows most-recent-first; test with 2+ rows across different dates AND two rows sharing the same date (confirming the no-uniqueness-constraint behavior from spec 44).
- `GET /cardio-log/weekly-summary`: test the Monday-start week-boundary explicitly — a row dated the most recent Sunday must NOT count toward a week that started the following Monday (mirror the exact boundary-test shape already used for missed-workout-handling's week-key tests, e.g. `tests/test_missed_days_detection.py`, adjusted for this endpoint). Test with 0, 1, and 2+ rows in the current week to confirm `count` is accurate and `target` is always `2`.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q`, no regressions.
