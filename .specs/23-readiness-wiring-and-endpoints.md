# Spec 23: Wire real readiness data into the phase gate + readiness/confirm endpoints

## Objective
Replace `run_analysis.py`'s dead `EngineStateInput(current_phase=phase)` construction with a real one built from `DailyReadiness` history (spec 21) and the pure `compute_*` functions (spec 22), and add the endpoints the client needs to submit a daily check-in and confirm a phase transition. Per `docs/superpowers/specs/2026-07-18-recovery-readiness-checkin-design.md` §3-4 (read it first).

## File targets
- Modify: `ironlog/persistence/run_analysis.py` (the `EngineStateInput(...)` construction, `run_analysis.py:309-310` in the current file — confirm the exact line numbers haven't shifted before editing).
- Modify: `ironlog/api/app.py` — three new endpoints (see below).
- New: `ironlog/schemas_readiness.py` (or add to an existing schemas file if this repo groups request/response models elsewhere — check how `schemas_capture.py` is organized and follow the same convention) for the request/response DTOs.
- New tests: `tests/test_readiness_endpoints.py`, plus new/modified cases in whatever test file currently covers `run_analysis.py`'s phase-gate behavior (search for existing `phase_transition_available`/`EngineStateInput` test coverage first — there may be none, given the design doc's finding that this path has never been exercised with real data).

## The fix

### 1. `run_analysis.py` — real `EngineStateInput`
Replace:
```python
engine_state=EngineStateInput(current_phase=phase),
```
with a construction that:
- Queries the trailing ~14-16 days of `DailyReadiness` rows (ordered by date), maps them to `DailyReadinessInput` (spec 22's dataclass — a thin field-for-field copy, this repo's established pattern for DB-row-to-pure-dataclass mapping, same as `LoggedSet`/`MovementAnalysisInput` already do elsewhere in this file).
- Calls `compute_bw_stable_2wk`/`compute_rhr_down`/`compute_sleep_ok`/`compute_subjective_ok` on that list. For `compute_rhr_down`'s `baseline` argument: use a longer trailing window (e.g. 60-90 days) mean resting HR as the baseline, excluding the trend window itself — document the exact windows chosen in a comment, this is a real numeric judgment call worth being explicit about.
- Resolves the RPE-creep and strength-bounce inputs from existing history: find how this file (or `stall.py`) already queries recent RPE readings / e1RM history for a movement and reuse that query shape rather than writing a new one from scratch — do not introduce a second way of pulling e1RM history if one already exists in this codebase.
- Passes today's (or most-recent-available) `DailyReadiness.bodyweight` as `EngineStateInput.bodyweight`, fixing the SAME "never wired to real data" gap for the existing CUT→STAB gate (this is a two-line fix riding along with the six-boolean fix, not separate scope — the design doc's investigation found `bodyweight` was equally dead).

### 2. New endpoints (`ironlog/api/app.py`)
- `GET /readiness/today` → `Optional[DailyReadinessOut]` — returns today's row if one exists, else `None`. Client uses this to decide whether to show the check-in card.
- `POST /readiness` (body: bodyweight?, resting_hr?, sleep_ok?, subjective_ok?) → `DailyReadinessOut` — upserts today's `DailyReadiness` row (get-or-create by today's date, update whichever fields are provided; do not null out fields the request omits if a row already exists for today — a partial resubmit must not erase an earlier field in the same day).
- `POST /engine-state/confirm-phase` (body: `to_phase`) → confirms and writes `EngineState.current_phase = to_phase`. This is the ONLY place `EngineState.current_phase` gets written — mirror `apply.py`'s "single write point" convention. Validate `to_phase` actually matches what the most recent analysis's `phase_transition_available` reported (reject/400 if the client tries to confirm a transition that was never actually offered — do not let the client jump the gate to an arbitrary phase).

Where does the client find out a transition is pending, so it knows to call the confirm endpoint? Check whatever endpoint already returns post-session/analysis state to the client (likely wherever `AnalysisResult` currently gets consumed after `run_analysis` runs) and add `phase_transition_available: Optional[str]` to that response if it isn't already exposed — do not invent a brand-new polling endpoint if an existing response can carry this field.

## Edge cases
- **`POST /readiness` same-day resubmission must upsert, never duplicate** — relies on spec 21's `date` uniqueness; use `get-or-create-then-update`, not a raw insert.
- **`POST /engine-state/confirm-phase` must reject an unavailable transition** — the phase actually being confirmed must match what the gate most recently reported as available; this prevents a stale/replayed client request from forcing a phase change the data no longer supports.
- **Do not auto-apply the phase transition anywhere** — `run_analysis`/`apply_analysis` must continue to only ever report `phase_transition_available`, never write `current_phase` themselves. The confirm endpoint is the sole write path, matching the notes/proposals pipeline's established "propose, never silently apply" convention elsewhere in this codebase.
- If `compute_rhr_down`'s baseline can't be established (insufficient history), the function already returns `False` per spec 22 — no special-casing needed here, just pass through whatever spec 22 computes.
- Do not touch `EngineState`'s stored six boolean columns at all in this spec — the design doc confirmed nothing reads them into this path; leave them exactly as they are (dead columns, a future cleanup decision, not this spec's job).

## Dependencies
Depends on spec 21 (needs the `DailyReadiness` model to query) AND spec 22 (needs the `compute_*` functions) both merged first.

## Verification
- New endpoint tests: `POST /readiness` upserts correctly on same-day resubmit without erasing untouched fields; `GET /readiness/today` returns `None` before any submission and the row after; `POST /engine-state/confirm-phase` writes `current_phase` only when the requested phase matches what's actually available, and 400s otherwise.
- `run_analysis.py` integration test: seed enough `DailyReadiness` history + qualifying `SetLog` data that all six gate conditions are genuinely true, run analysis, and assert `phase_transition_available == Phase.REBUILD` — this is the regression test proving the gate can now actually fire (something no existing test currently proves, per the design doc's investigation).
- A second integration test with insufficient/mixed readiness data asserts `phase_transition_available` stays `None` — proving the gate doesn't fire on partial/absent data.
- Full server suite green: `.venv/bin/pytest -q` on myflix.

## Human gate note
This spec touches a stated invariant (the phase-transition write path) and introduces new API surface — route through Opus review unconditionally regardless of how clean the diff looks, same standard this codebase already applies to its other safety-relevant mechanisms (e.g. the notes/proposals resolver). Not itself a schema change or dependency bump, so no HUMAN GATE pause is required at merge, but do not classify this spec as review-exempt under any circumstance.
