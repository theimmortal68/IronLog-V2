# Spec 31: `compute_goal_stable` pure function

## Objective
Add the pure, DB-free function that checks whether a trailing week of readings has stayed consistently at/below a target (a single rebound-above day disqualifies), per the design doc §Components 2. This is the new stability check the goal-driven phase gate needs — the athlete explicitly wants a transient 2-3lb (or 2%) drop that bounces back the next day to NOT fire the gate.

## File targets
- Modify: `ironlog/engine/readiness.py` (add the new function alongside the existing `compute_bw_stable_2wk`/`compute_rhr_down`/etc — mirror this module's exact conventions: `as_of`-anchored windowing, insufficient-data-means-False invariant).
- New tests: extend `tests/test_readiness_compute.py` (this repo's existing test file for this module) with new test cases — do not create a separate test file.

## The fix

```python
def compute_goal_stable(
    rows: List[DailyReadinessInput],
    as_of: date,
    field: str,
    target: float,
    tolerance: float,
    window_days: int = 7,
    min_readings: int = 4,
) -> bool:
    """True iff at least min_readings of the trailing window_days (anchored
    on as_of, same both-bounds-inclusive windowing as _trailing_rows) have
    a non-null value for `field` ("bodyweight" or "body_fat_pct") AND
    EVERY one of those readings is <= target + tolerance. A single
    reading above target+tolerance anywhere in the window -- e.g. a
    rebound the day after a transient drop -- fails the check. This is
    a sustained-state check, not a single-point-in-time check and not
    an average. Insufficient data -> False, same invariant as every
    other compute_* function in this module.

    `field` selects which DailyReadinessInput attribute to check
    (getattr(row, field)) so this one function serves both the weight
    goal and the body-fat % goal without duplicating the windowing
    logic."""
```

Reuse this module's existing `_trailing_rows` helper (already fixed for staleness in the recovery/readiness batch — anchored on `as_of`, both bounds inclusive: `cutoff <= row.date <= as_of`) for the windowing — do not write a new windowing implementation.

## Edge cases
- Empty input list → `False`.
- Fewer than `min_readings` qualifying (non-null `field`) readings in the window → `False`, not an exception.
- A single day of data (even if it clears the target) → `False` if it's below `min_readings` (the default `min_readings=4` already enforces this, just confirm the boundary is tested).
- Exactly `min_readings` qualifying readings, all `<= target + tolerance` → `True`.
- A reading exactly equal to `target + tolerance` → counts as satisfying (inclusive `<=`, not strict `<`).
- One qualifying reading strictly greater than `target + tolerance` among an otherwise-sufficient window → `False` (this is the rebound-day scenario — the specific case the athlete described and the whole reason this function exists; write a test that mirrors it concretely, e.g. 6 days at/under target then 1 day 3 lb over).
- `field="body_fat_pct"` must work identically to `field="bodyweight"` — the function must not have any bodyweight-specific logic baked in (it's meant to serve both goal types via the same code path).

## Dependencies
None — standalone, no shared files with spec 30 (parallel-safe; different file, and `compute_goal_stable` has no reference to `GoalSettings` at all, it's a pure function operating on values passed in).

## Verification
- New tests covering: sufficient-stable-week → `True`; the rebound-day scenario → `False` (the specific, named test case above); insufficient data → `False`; boundary at exactly `target + tolerance` → counts as passing; `field="body_fat_pct"` works identically to `field="bodyweight"` (test both).
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 586 passing).
