# Goal-Driven Phase Gate Thresholds — Design

## Problem

The CUT→STAB phase gate (`ironlog/engine/analysis.py`'s `_evaluate_phase_gate`) checks `bodyweight <= cut_to_stab_target + cut_to_stab_tolerance`, but `cut_to_stab_target`/`cut_to_stab_tolerance` are **hardcoded dataclass defaults** (`213.0`/`2.0` on `EngineStateInput`) — never read from any DB config, never user-settable anywhere. This was discovered during the Withings body-scan integration batch (2026-07-18/19) and deliberately deferred into its own design, since it touches a stated engine invariant (the phase-transition gate) rather than being a simple data-sync change.

Separately, `DailyReadiness.body_fat_pct` (live via the Withings integration, merged 2026-07-19) has zero gate consumers — it's pure capture. This design also decides what a body-fat-% goal should drive.

## Scope decisions (from brainstorming)

- **Same shape as today, made real**: keep target-weight + tolerance (not a target-date/rate-of-loss — that's a materially bigger feature, out of scope here).
- **New dedicated `GoalSettings` table**, not new `EngineState` fields — more isolated, and naturally houses the body-fat goal too.
- **Body-fat % IS a load-bearing gate criterion, not just a display value** (expanded mid-brainstorming from the original "storage only" framing) — the athlete wants to be able to target either weight or body-fat %, since body recomposition can hit a body-fat goal before the scale number reflects it.
- **Combination logic: OR.** The gate fires if bodyweight clears its target-and-stability check, **or** body-fat % clears its own (when a body-fat goal is actually set) — whichever the athlete is tracking toward.
- **Symmetric tolerance**: body-fat % gets its own tolerance field, same reasoning as the existing weight tolerance (bioimpedance readings have day-to-day noise).
- **Stability requirement (new, not in the original hardcoded check at all)**: a single day's reading clearing the target is not enough — real-world weigh-ins occasionally show a 2-3lb (or 2%) drop that rebounds the next day, and that must not fire the gate. The check requires the trailing week's readings to **all** stay at/below target+tolerance — one rebound-above day anywhere in the window fails it. This is the strictest interpretation (not "6 of 7 days," a single exception anywhere disqualifies).
- **Migration seeds real day-1 values** (`213.0`/`2.0` for weight) so deploy day has zero behavior change from what's live today. Body-fat goal fields start `NULL` (opt-in, no current equivalent to seed from).

## Components

### 1. `GoalSettings` model + migration

New singleton table (`ironlog/models/library.py`, mirrors `EngineState`/`WithingsCredentials`'s `id=1` pattern):

```python
class GoalSettings(SQLModel, table=True):
    """Singleton (id==1) holding athlete-settable phase-gate goals.
    target_bodyweight/tolerance seeded at migration time with the values
    that were previously hardcoded on EngineStateInput -- zero behavior
    change on deploy. target_body_fat_pct/tolerance start unset (no
    prior equivalent existed)."""
    id: Optional[int] = Field(default=1, primary_key=True)
    target_bodyweight: float
    target_bodyweight_tolerance: float
    target_body_fat_pct: Optional[float] = None
    target_body_fat_pct_tolerance: Optional[float] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

Migration: `CREATE TABLE` + a seed `INSERT` for the singleton row with `target_bodyweight=213.0, target_bodyweight_tolerance=2.0`. Per this repo's migration-authoring rules, the `INSERT` (a data change, not pure schema) must be single-statement/idempotent on its own — likely its own follow-up statement or a second migration file, decided at spec-writing time based on the exact carve-out rules in `deploy/migrations/README.md`.

### 2. New stability-checked pure function

`ironlog/engine/readiness.py` gains a new function alongside the existing `compute_*` functions:

```python
def compute_goal_stable(
    rows: List[DailyReadinessInput],
    as_of: date,
    field: str,           # "bodyweight" or "body_fat_pct"
    target: float,
    tolerance: float,
    window_days: int = 7,
    min_readings: int = 4,
) -> bool:
    """True iff at least min_readings of the trailing window_days have a
    non-null value for `field` AND every one of those readings is
    <= target + tolerance. A single reading above target+tolerance
    anywhere in the window (e.g. a rebound the day after a transient
    drop) fails the check -- this is a sustained-state check, not a
    single-point-in-time or average check. Insufficient data -> False,
    same invariant as every other compute_* function in this module."""
```

Reuses this module's existing `_trailing_rows`-style windowing helper (the same `as_of`-anchored, both-bounds-inclusive windowing already fixed for staleness in the recovery/readiness batch) rather than a new one.

### 3. Gate logic change

`_evaluate_phase_gate`'s CUT branch (`ironlog/engine/analysis.py`) changes from:
```python
if es.bodyweight is not None and es.bodyweight <= es.cut_to_stab_target + es.cut_to_stab_tolerance:
    return Phase.STAB
```
to something evaluating both stability results (computed upstream in `run_analysis.py` and passed in via `EngineStateInput`, keeping `analysis.py` itself pure/DB-free):
```python
if es.weight_goal_stable or (es.target_body_fat_pct is not None and es.body_fat_goal_stable):
    return Phase.STAB
```
Exact field names on `EngineStateInput` decided at spec-writing time (likely `weight_goal_stable: bool` / `body_fat_goal_stable: bool` replacing the old `bodyweight`/`cut_to_stab_target`/`cut_to_stab_tolerance` fields, since the stability computation now happens before construction, not inside the gate itself).

### 4. Wiring in `run_analysis.py`

Reads `GoalSettings(id=1)`, reuses the same trailing `DailyReadiness` query already built for the STAB→REBUILD signals (spec 23), computes `compute_goal_stable(...)` for bodyweight always, and for body-fat % only when `target_body_fat_pct` is set. Passes both booleans into `EngineStateInput`.

### 5. Settings endpoint

`GET /goals` (current `GoalSettings` row) + `POST /goals` (partial upsert, `exclude_unset`-style — same pattern as `POST /readiness` and `POST /integrations/withings/...`, so updating just `target_bodyweight` doesn't null out a configured body-fat goal).

## Data flow

```
[GoalSettings(id=1)] ──┐
                        ├──▶ run_analysis.py: compute_goal_stable() for
[DailyReadiness rows] ──┘    bodyweight (always) + body_fat_pct (if goal set)
                                    │
                                    ▼
                        EngineStateInput(weight_goal_stable=..., body_fat_goal_stable=...)
                                    │
                                    ▼
                        _evaluate_phase_gate(): OR of both -> Phase.STAB or None
```

## Testing approach

- Pure unit tests for `compute_goal_stable`: sufficient-stable-week → True; a rebound-above-target day anywhere in the week → False (the exact scenario from the design conversation); insufficient data → False; boundary case at exactly `target + tolerance`.
- `_evaluate_phase_gate` unit tests: weight-only stable → STAB; body-fat-only stable (no weight goal met) → STAB; neither stable → None; body-fat goal not set at all → behaves identically to weight-only (no regression).
- `run_analysis.py` integration test: seed `GoalSettings` + a week of `DailyReadiness` rows, confirm the gate fires/doesn't fire matching real data, mirroring the pattern established for the STAB→REBUILD integration test in the recovery/readiness batch.
- Migration test: confirm the seeded row's values exactly match today's pre-migration hardcoded constants (213.0/2.0) — this is the regression proof that deploy day changes nothing until the athlete acts.

## Explicitly out of scope

- Target date / rate-of-loss pacing (e.g. "on track" vs "behind schedule") — a materially bigger feature than a threshold+stability check.
- Any client-side goal-setting UI — server-first, same pattern as every other feature this session (verify real endpoint shapes via a live curl before specing a client follow-on).
- Changing the STAB→REBUILD gate's six-boolean logic — untouched by this design.
- A body-fat-% AND-only mode, or an athlete-selectable single-active-goal-type mode — the brainstorming session settled on OR combination; not building the alternatives.
