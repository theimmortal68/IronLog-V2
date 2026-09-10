# Spec 38: GET /weak-points endpoint

## Objective
Add the read endpoint that surfaces the latest weak-point assessment: a muscle-group summary (primary-muscle-only rollup) with the specific weak movements underneath each group, per the design doc §Components 4.

## File targets
- New: `ironlog/api/schemas_weakpoints.py` (response DTOs — mirror `ironlog/api/schemas_readiness.py`'s/`schemas_goals.py`'s exact convention/style).
- Modify: `ironlog/api/app.py` — add one endpoint.
- New tests: `tests/test_weak_points_endpoint.py`.

## The fix

### `ironlog/api/schemas_weakpoints.py`
```python
"""Weak-point assessment API contract."""
from typing import List, Optional
from pydantic import BaseModel

class WeakMovementOut(BaseModel):
    movement_id: int
    name: str
    stalled: bool
    lagging: bool
    growth_rate: Optional[float] = None

class MuscleGroupSummaryOut(BaseModel):
    muscle: str
    weak_count: int
    total_count: int
    weak_movements: List[WeakMovementOut]

class WeakPointAssessmentOut(BaseModel):
    muscle_groups: List[MuscleGroupSummaryOut]
    movements: List[WeakMovementOut]
```

### `ironlog/api/app.py` — one new endpoint
```python
@app.get("/weak-points", response_model=WeakPointAssessmentOut)
def get_weak_points(db: Session = Depends(get_session)):
    """Returns the latest weak-point assessment: the most recent
    MovementWeaknessSignal row per movement (by computed_at), plus a
    muscle-group rollup computed live over those rows (primary muscle
    only -- Movement.secondary_muscles do NOT count toward the
    rollup, per the design decision).

    'Latest per movement' means: for movements with multiple
    MovementWeaknessSignal rows (this is an append-only history
    table, spec 36), only the row with the max(computed_at) for each
    movement_id is used -- older rows are ignored, not deleted."""
    ...
```

Implementation approach: query all `MovementWeaknessSignal` rows, group by `movement_id` in Python, keep only the max-`computed_at` row per group (or use a SQL window/subquery if you're confident in the exact SQLModel/SQLAlchemy syntax — a simple Python-side reduction is acceptable and clearer for this table's expected size). For each latest row, join to `Movement` (for `name` and `primary_muscle`). Build the `movements` list (every movement with a signal row, weak or not — the response includes ALL assessed movements, not just weak ones, so the client can show a complete picture). Build `muscle_groups` by grouping the SAME movements by `Movement.primary_muscle`, computing `weak_count`/`total_count`, and `weak_movements` = only the ones with `is_weak=True` in that group. A movement with `primary_muscle=None` (if any exist) should be excluded from `muscle_groups` entirely (nothing to group it under) but still appear in the flat `movements` list.

## Edge cases
- **No `MovementWeaknessSignal` rows exist yet** (feature just deployed, no session analyzed since) — return `WeakPointAssessmentOut(muscle_groups=[], movements=[])`, not a 404 or error.
- **A movement has signal rows but `Movement.primary_muscle` is `None`** — excluded from `muscle_groups`, still present in `movements`.
- **Multiple rows for the same movement** — only the latest (`max(computed_at)`) is used; do not accidentally count a movement twice in `total_count` because of duplicate history rows.
- **A muscle group where `weak_count == 0`** — still included in `muscle_groups` (with `weak_movements=[]`) if at least one movement is tracked for it, not filtered out — the client needs to see "this muscle group has no weak points" as distinct from "this muscle group was never assessed."
- Do NOT touch `MovementWeaknessSignal`'s writer (`run_analysis.py`, a separate spec) — this spec is read-only.

## Dependencies
Depends on spec 36 (`MovementWeaknessSignal` model) merged first. Does NOT depend on spec 37 (the writer) — this endpoint can be written and tested against directly-seeded rows, same pattern as spec 33 (`GET /goals`) not depending on the phase-gate-wiring spec that populated `GoalSettings` in production. Parallel-safe with spec 37 (no shared files: this spec touches `ironlog/api/app.py` + new `schemas_weakpoints.py`; spec 37 touches `ironlog/persistence/run_analysis.py`).

## Verification
- New tests: empty-state response before any rows exist; a seeded scenario with multiple movements across multiple muscle groups, some weak/some not, confirming both `muscle_groups` and `movements` are correct; a movement with multiple history rows confirming only the latest is used; a movement with `primary_muscle=None` confirming it's excluded from `muscle_groups` but present in `movements`.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 606 passing, or higher if spec 36 already merged in this batch — check `main`'s actual count before dispatching).
