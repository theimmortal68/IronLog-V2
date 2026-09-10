# Spec 22: Pure readiness-computation functions

## Objective
Add the pure, DB-free functions that derive all six STAB→REBUILD gate booleans — four from recent daily-check-in trends, two from existing workout-performance history — per `docs/superpowers/specs/2026-07-18-recovery-readiness-checkin-design.md` §2 (read it first).

## File targets
- New: `ironlog/engine/readiness.py` (mirrors the pure-function shape of `ironlog/engine/stall.py` and `ironlog/engine/analysis.py` — no DB access, no network, plain dataclasses/lists in, booleans out).
- New tests: `tests/test_readiness_compute.py`.

## The fix
```python
@dataclass
class DailyReadinessInput:
    """One day's readiness data, decoupled from the DailyReadiness DB model
    (spec 21) so this module stays testable without a database — mirrors how
    analysis.py's EngineStateInput/LoggedSet are decoupled from their DB rows."""
    date: date
    bodyweight: Optional[float] = None
    resting_hr: Optional[float] = None
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None


def compute_bw_stable_2wk(rows: List[DailyReadinessInput], tolerance: float = 2.0) -> bool
def compute_rhr_down(rows: List[DailyReadinessInput], baseline: Optional[float]) -> bool
def compute_sleep_ok(rows: List[DailyReadinessInput], min_good_ratio: float = 0.7) -> bool
def compute_subjective_ok(rows: List[DailyReadinessInput], min_good_ratio: float = 0.7) -> bool
def compute_no_rpe_creep(recent_rpe_readings: List[float]) -> bool
def compute_strength_bounce(e1rm_history: List[Tuple[date, float]]) -> bool
```

Behavior for each:
- **`compute_bw_stable_2wk`**: True iff there are at least ~10 of the trailing 14 days with a non-null `bodyweight` value AND the range (max-min) of those values is within `tolerance` lb. Insufficient data (fewer qualifying days) → False, not an exception and not True-by-default.
- **`compute_rhr_down`**: True iff `baseline` is not None AND the mean of the trailing week's non-null `resting_hr` readings is meaningfully below `baseline` (pick a concrete margin — e.g. 3+ bpm — and document your choice in a docstring/comment; this is a real numeric judgment call, make it explicit rather than a magic unexplained number). No baseline or no recent readings → False.
- **`compute_sleep_ok`** / **`compute_subjective_ok`**: True iff at least a handful of the trailing ~10 days have a non-null value for the field AND the fraction of `True` values among those is ≥ `min_good_ratio`. Insufficient data → False.
- **`compute_no_rpe_creep`**: True iff the trailing RPE readings (already resolved by the caller — this function does not query SetLog itself, the caller passes the relevant numeric series) show no sustained upward trend beyond a reasonable noise band. Keep the trend test simple and explainable (e.g. compare the mean of the most recent third of the series against the mean of the earliest third) — this doesn't need to be statistically sophisticated, just correct and testable.
- **`compute_strength_bounce`**: True iff the e1RM history (date, value) pairs show a recovering/upward trend over the trailing window after having previously been flat-or-declining (a "bounce back," not just "still going up" — a lift that's been climbing the whole time doesn't need REBUILD, it's already progressing). Keep this simple and explainable, same standard as above.

**Every function returns `False` on insufficient data — never `True` by default or by absence of contrary evidence.** The STAB→REBUILD gate must never fire because a signal was silently ignored.

## Edge cases
- Empty input lists → False for every function (not an exception).
- A single day of data → False for all the trend-based functions (a trend needs multiple points; document the minimum data requirement per function in its docstring).
- Do not import or reference `DailyReadiness` (the SQLModel table from spec 21) anywhere in this file — the whole point of `DailyReadinessInput` is DB-independence. This spec has no dependency on spec 21 and can be built/tested in complete isolation from it.

## Dependencies
None — standalone, no shared files with spec 21 (parallel-safe).

## Verification
- New tests covering: sufficient-data true case and false case for each of the 6 functions; insufficient-data → False for each; the exact numeric thresholds you choose (document them, then test them at the boundary).
- Full server suite green: `.venv/bin/pytest -q` on myflix.
