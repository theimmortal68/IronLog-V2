# Spec 35: compute_growth_rate + compute_lagging pure functions

## Objective
Add the pure functions that compute a movement's e1RM growth rate and compare it against the athlete's median growth rate across other movements, per `docs/superpowers/specs/2026-07-19-weak-point-assessment-design.md` (read it first).

## File targets
- Modify: `ironlog/engine/stall.py` (add the two new functions + two new constants, alongside the existing `detect_stall`/`StallSignal` — same pure, DB-free style as this whole module).
- New tests: extend `tests/test_stall.py` (this repo's existing test file for this module — check it exists; if this module has no dedicated test file, check `tests/test_analysis.py` or wherever `detect_stall` is actually tested and mirror that location) with new test cases — do not create a separate test file if an existing one already covers `stall.py`.

## The fix

```python
LAG_THRESHOLD_PCT = 0.05
LAG_MIN_COMPARISON_MOVEMENTS = 3


def compute_growth_rate(progress_anchor_e1rms: List[float]) -> Optional[float]:
    """(latest - oldest) / oldest over the given e1RM window. Same
    PROGRESS-anchor window shape detect_stall already consumes (the
    caller does window selection, this function doesn't). Returns
    None if fewer than 2 data points (can't compute a rate) or if the
    oldest value is 0 (avoid a ZeroDivisionError -- a 0 e1RM shouldn't
    occur in practice but guard it anyway)."""
    ...


def compute_lagging(
    this_movement_rate: Optional[float],
    other_movement_rates: List[float],
) -> bool:
    """True iff this_movement_rate is not None, at least
    LAG_MIN_COMPARISON_MOVEMENTS values are present in
    other_movement_rates (None values in that list don't count toward
    the minimum -- filter them out first), and
    median(other_movement_rates) - this_movement_rate >= LAG_THRESHOLD_PCT.
    False on any insufficient-data case -- never True by default,
    matching the invariant every other compute_* function in this
    codebase's engine layer follows (see ironlog/engine/readiness.py's
    compute_* functions for the same convention)."""
    ...
```

`other_movement_rates` may contain `None` entries (a movement in the comparison population might itself have insufficient data for its own rate) — filter those out before checking the minimum-count and before computing the median. Use `statistics.median` from the standard library.

## Edge cases
- `compute_growth_rate`: empty list or single-element list → `None`. Oldest value `<= 0` → `None` (avoid div-by-zero; a real e1RM should never be non-positive, but don't crash if it somehow is).
- `compute_lagging`: `this_movement_rate=None` → `False` immediately, don't even look at `other_movement_rates`.
- `compute_lagging`: `other_movement_rates` with fewer than 3 non-`None` values → `False` (insufficient comparison population), even if the raw list has more than 3 entries total (some may be `None`).
- `compute_lagging`: a movement's own growth rate exactly `LAG_THRESHOLD_PCT` below the median → `True` (inclusive boundary, `>=` not `>`) — write a boundary test for this exact case.
- `compute_lagging`: a movement's own growth rate ABOVE the median (better than average) → `False`, obviously, but write the test anyway for completeness.

## Dependencies
None — standalone, no shared files with spec 36 (parallel-safe; different files entirely, `ironlog/engine/stall.py` vs `ironlog/models/library.py`).

## Verification
- New tests covering every edge case above, plus a "normal" case (a genuinely lagging movement in a realistic comparison population, and a genuinely non-lagging one).
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 606 passing).
