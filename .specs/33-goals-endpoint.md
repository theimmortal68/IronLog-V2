# Spec 33: `GET`/`POST /goals` endpoints

## Objective
Add the settings endpoints for reading and updating the athlete's `GoalSettings` (spec 30, must be merged first), per the design doc §Components 5.

## File targets
- New: `ironlog/api/schemas_goals.py` (request/response DTOs — mirror `ironlog/api/schemas_readiness.py`'s exact convention/style).
- Modify: `ironlog/api/app.py` — add two endpoints (see below).
- New tests: `tests/test_goals_endpoint.py`.

## The fix

### `ironlog/api/schemas_goals.py`
```python
"""Goal settings API contract."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class GoalSettingsOut(BaseModel):
    target_bodyweight: float
    target_bodyweight_tolerance: float
    target_body_fat_pct: Optional[float] = None
    target_body_fat_pct_tolerance: Optional[float] = None
    updated_at: datetime

class GoalSettingsIn(BaseModel):
    target_bodyweight: Optional[float] = None
    target_bodyweight_tolerance: Optional[float] = None
    target_body_fat_pct: Optional[float] = None
    target_body_fat_pct_tolerance: Optional[float] = None
```

### `ironlog/api/app.py` — two new endpoints
```python
@app.get("/goals", response_model=Optional[GoalSettingsOut])
def get_goals(db: Session = Depends(get_session)):
    """Returns the current GoalSettings row, or None if never configured
    (the athlete hasn't set anything yet -- the phase gate itself falls
    back to 213.0/2.0 internally per spec 32, but this endpoint reports
    the true DB state, not a synthesized default, so the client can
    distinguish "never configured" from "configured to exactly the
    historical defaults")."""
    ...

@app.post("/goals", response_model=GoalSettingsOut)
def post_goals(req: GoalSettingsIn, db: Session = Depends(get_session)):
    """Get-or-create-then-selectively-update, same exclude_unset upsert
    pattern as POST /readiness (search for `def post_readiness` in this
    file and mirror it exactly) -- a partial update (e.g. only
    target_body_fat_pct) must not null out fields the request omits.
    On first-ever creation (no existing row), target_bodyweight and
    target_bodyweight_tolerance are REQUIRED in the request body (the
    model has no default for them) -- if either is missing on a
    first-time POST, return a clear 422/400 rather than a raw
    constructor error."""
    ...
```

## Edge cases
- **`GET /goals` before any row exists** — returns `None` (not a 404, not a synthesized default) — matches `GET /readiness/today`'s existing "return None when nothing exists yet" convention.
- **`POST /goals` first-ever call missing `target_bodyweight`/`target_bodyweight_tolerance`** — must fail with a clear error, not an unhandled exception, since those two fields have no default on the model.
- **`POST /goals` partial update on an EXISTING row** (e.g. only `target_body_fat_pct` in the body) — must not null out `target_bodyweight`/tolerance or any other already-set field. Use the exact `exclude_unset` pattern already established.
- **Setting `target_body_fat_pct` to a real value while `target_body_fat_pct_tolerance` is omitted** — leave `target_body_fat_pct_tolerance` as whatever it already was (likely `None` on first set) — spec 32's gate-wiring code is responsible for defaulting a `None` tolerance to `0.0` at read time, not this endpoint.
- Do NOT touch the phase-gate wiring itself (spec 32) — this spec is CRUD-only.

## Dependencies
Depends on spec 30 (`GoalSettings` model) merged first. Does not depend on spec 31 or 32 (no shared files — this spec only touches `ironlog/api/app.py` and new schema/test files, spec 32 touches `ironlog/engine/analysis.py`/`ironlog/persistence/run_analysis.py`).

## Verification
- New tests: `GET /goals` returns `None` before any row exists; `POST /goals` creates a row on first call (with both required weight fields present) and returns it; `POST /goals` first-call missing a required weight field returns a clear 4xx, not a 500; a second `POST /goals` with only `target_body_fat_pct` in the body preserves the existing `target_bodyweight`/tolerance values (the partial-upsert regression test); `GET /goals` after a `POST` reflects the updated values.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 586 passing, or higher if spec 30 already merged in this batch — check `main`'s actual count before dispatching).
