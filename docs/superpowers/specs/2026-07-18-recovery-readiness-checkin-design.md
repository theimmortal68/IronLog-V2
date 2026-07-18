# Recovery/Readiness Check-in — Design

**Date:** 2026-07-18
**Status:** Approved, pre-implementation
**Repos:** IronLog-V2 (server, primary) + IronLog-V2-Client (client, daily check-in UI)

## Problem

`EngineState`'s STAB→REBUILD gate (`analysis.py::_evaluate_phase_gate`) requires six boolean readiness signals (`rhr_down`, `sleep_ok`, `no_rpe_creep`, `bw_stable_2wk`, `strength_bounce`, `subjective_ok`) to all be true. Today this gate is **fully inert end-to-end**:

- `run_analysis.py` constructs `EngineStateInput(current_phase=phase)` — every other field, including all six booleans and `bodyweight`, is left at its dataclass default (`False`/`None`). The gate has never evaluated against real data.
- `EngineState`'s own stored boolean columns (`ironlog/models/library.py`) have no API endpoint to set them — the only way to change them today is a direct DB edit.
- `AnalysisResult.phase_transition_available` (the gate's output) is computed and returned but nothing downstream — no endpoint, no client screen — ever reads it. It has no consumer.

This is not a "repair a manually-maintained flag" task. It's building the first real, end-to-end version of a mechanism that has existed only as inert scaffolding.

## Scope decisions (from brainstorming)

- **Auto-compute what's derivable.** `no_rpe_creep` and `strength_bounce` come from existing `SetLog`/e1RM history — no new athlete input needed for those two.
- **Wearable-ready, not wearable-built.** Polar Verity Sense + Samsung Watch integration is real future scope, but out of this spec. The data model must accept a device-sourced row later without a schema change; the actual Health Connect / Polar BLE work is its own future spec.
- **Daily, not workout-gated.** The check-in is prompted once per day regardless of whether the athlete trains, since bodyweight-stability and RHR/sleep trends need rest-day data too.
- **Numeric where measurable, boolean where subjective.** Bodyweight and resting HR are numbers the server trends over time; sleep quality and general feel are simple good/not-good toggles.
- **Report, never auto-apply.** Matches the established pattern elsewhere in this codebase (the notes/proposals pipeline never silently mutates state) — a phase transition becoming available must be surfaced for explicit athlete confirmation, never auto-written.

## Components

### 1. `DailyReadiness` model (new, `ironlog/models/library.py` or a new `readiness.py`)

One row per calendar day:

```python
class DailyReadiness(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date = Field(index=True, unique=True)
    bodyweight: Optional[float] = None
    bodyweight_source: str = "manual"           # "manual" | future: "samsung_health" | "polar"
    resting_hr: Optional[float] = None
    resting_hr_source: str = "manual"           # "manual" | future: "samsung_health" | "polar"
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

One row per day (unique on `date`) so a later submission the same day updates in place rather than creating a duplicate — the client's daily-check-in-card logic (shown only if today has no row yet) depends on this being a clean 1:1.

The `_source` fields are the wearable-ready seam: a future Health Connect/Polar sync job writes rows with `source="samsung_health"` etc.; the computation functions in §2 don't care about source, only about the numeric/boolean value and the date.

### 2. Pure computation functions (new module, `ironlog/engine/readiness.py`, mirrors `stall.py`'s pure-function shape)

```python
def compute_bw_stable_2wk(rows: List[DailyReadinessInput], tolerance: float = 2.0) -> bool
def compute_rhr_down(rows: List[DailyReadinessInput], baseline: Optional[float]) -> bool
def compute_sleep_ok(rows: List[DailyReadinessInput], min_good_ratio: float = 0.7) -> bool
def compute_subjective_ok(rows: List[DailyReadinessInput], min_good_ratio: float = 0.7) -> bool
def compute_no_rpe_creep(recent_rpe_readings: List[float]) -> bool
def compute_strength_bounce(e1rm_history: List[Tuple[date, float]]) -> bool
```

All pure (no DB access), taking plain dataclasses/lists — same pattern `analysis.py` and `stall.py` already use, so they're unit-testable without a database. `rhr_down`/`sleep_ok`/`subjective_ok` return `False` (not "unknown") when there isn't enough trailing data to judge — the gate should never pass on absence of data, only on a real trend.

Exact tolerance/window constants (14-day bodyweight window, RPE-creep lookback, e1RM bounce-back window) are implementation-plan detail, not design-doc detail — the plan will pin these down with the athlete's actual historical data as a sanity check.

### 3. Wire into `run_analysis.py`

Replace:
```python
engine_state=EngineStateInput(current_phase=phase)
```
with a real construction that:
- queries the last 14+ days of `DailyReadiness` rows,
- calls the six `compute_*` functions above,
- pulls `no_rpe_creep`/`strength_bounce` inputs from existing `SetLog`/`E1rmHistory` queries (reusing whatever `stall.py` already queries — no new historical-data plumbing needed there),
- passes real `bodyweight` (today's `DailyReadiness.bodyweight`, or the most recent available) for the existing CUT→STAB gate, which has the same "never wired to real data" problem.

This is the one change that makes the phase gate live for the first time.

### 4. Phase-transition confirmation (new)

When `phase_transition_available` is non-null after analysis, it needs a home:
- A new field on whatever response already surfaces post-session state to the client (or a small dedicated endpoint, `GET /engine-state/pending-transition`).
- A confirm action (`POST /engine-state/confirm-phase`) that writes `EngineState.current_phase` — the only write path, mirroring `apply.py`'s "single write point" convention already established for `MovementState`.
- Client surfaces this the same way the Review screen already surfaces other things needing confirmation (exact placement — new banner vs. folded into Review — is implementation-plan/UI detail, not architecture).

### 5. Client — daily check-in

- New small screen or Today-screen card: bodyweight (number), resting HR (number, optional), sleep good/not-good (toggle), general feel good/not-good (toggle).
- Shown once per day — only if `GET /readiness/today` (new, thin) returns no row yet for today's date.
- Submits to `POST /readiness` (new), which upserts today's `DailyReadiness` row.

## Data flow

```
Athlete opens app (any day)
  -> Today screen checks GET /readiness/today
  -> if none: show check-in card
  -> athlete submits -> POST /readiness -> upserts today's DailyReadiness row

Athlete logs a session (Capture -> Finish)
  -> run_analysis.py now builds a REAL EngineStateInput
     (compute_* functions read DailyReadiness + SetLog/e1RM history)
  -> analysis.py's existing _evaluate_phase_gate runs unchanged, against real data
  -> AnalysisResult.phase_transition_available may now actually be non-null

Client next opens app / views session result
  -> sees the pending phase transition (new surface)
  -> explicit confirm -> POST /engine-state/confirm-phase -> writes EngineState.current_phase
```

## Testing

- `compute_*` functions: pure unit tests, no DB — feed hand-built `DailyReadinessInput` lists / RPE lists / e1RM histories, assert the boolean.
- `run_analysis.py` integration: a test session with real `DailyReadiness` history + qualifying `SetLog`s should now produce a non-null `phase_transition_available` where before this fix it never could (this is the regression test proving the gate actually fires).
- New endpoints: standard FastAPI test-client coverage (`POST /readiness` upserts correctly on same-day resubmit; `POST /engine-state/confirm-phase` writes `current_phase` and only that field).
- Client: unit tests for the check-in card's "show only if today has no row" logic; no new Compose UI test infra needed (matches this codebase's existing "manual on-device verification for the visual, unit test the pure logic" pattern).

## Explicitly out of scope

- Polar Verity Sense / Samsung Watch device pairing and sync (future spec; this design's `*_source` fields are the seam it will plug into).
- Any UI for viewing readiness trends over time (a natural follow-on to the Phase-0 progress-charts roadmap item, not this spec).
- Changing the CUT→STAB or STAB→REBUILD gate *thresholds* themselves (tolerance values, window lengths) beyond making them real inputs — that's a training-philosophy question for the athlete to weigh in on later if the computed values don't match their expectations in practice.
