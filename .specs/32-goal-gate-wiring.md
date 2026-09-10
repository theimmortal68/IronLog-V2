# Spec 32: Wire GoalSettings + compute_goal_stable into the CUT→STAB gate

## Objective
Replace the CUT→STAB gate's hardcoded `bodyweight <= cut_to_stab_target + cut_to_stab_tolerance` check with a real OR of two stability-checked goal criteria (weight, and optionally body-fat %), fed by `GoalSettings` (spec 30, must be merged first) and `compute_goal_stable` (spec 31, must be merged first). Per the design doc §Components 3-4 (read it first).

## File targets
- Modify: `ironlog/engine/analysis.py` — `EngineStateInput` field changes + `_evaluate_phase_gate`'s CUT branch (see below).
- Modify: `tests/test_analysis.py` — the existing `make_engine_state` test helper and 3 existing CUT-gate tests (`test_cut_to_stab_gate_met`, `test_cut_to_stab_gate_not_met_when_too_heavy`, `test_cut_to_stab_gate_none_bodyweight_is_not_satisfied`) reference the OLD fields (`bodyweight`, `cut_to_stab_target`, `cut_to_stab_tolerance`) being removed in this spec — update them to the new field shape, do not leave them broken or delete them without replacement coverage.
- Modify: `ironlog/persistence/run_analysis.py` — read `GoalSettings`, compute both stability results, construct `EngineStateInput` with the new fields.
- New tests: extend `tests/test_run_analysis_phase_gate.py` (this repo's existing integration test file for the phase gate, from the recovery/readiness batch) with new cases covering the goal-driven behavior.

## The fix

### 1. `ironlog/engine/analysis.py` — `EngineStateInput` field changes

**Remove**: `bodyweight`, `cut_to_stab_target`, `cut_to_stab_tolerance` (all three — confirmed via grep that `bodyweight` on this dataclass has exactly one consumer, the CUT gate check being replaced here; no other code path reads it).

**Add**:
```python
weight_goal_stable: bool = False
body_fat_goal_configured: bool = False   # True iff GoalSettings.target_body_fat_pct is set
body_fat_goal_stable: bool = False        # only meaningful when body_fat_goal_configured is True
```

`_evaluate_phase_gate`'s CUT branch changes from:
```python
if es.bodyweight is not None and es.bodyweight <= es.cut_to_stab_target + es.cut_to_stab_tolerance:
    return Phase.STAB
return None
```
to:
```python
if es.weight_goal_stable or (es.body_fat_goal_configured and es.body_fat_goal_stable):
    return Phase.STAB
return None
```
`analysis.py` stays pure/DB-free — it never calls `compute_goal_stable` or reads `GoalSettings` itself, it only consumes the two booleans a caller (`run_analysis.py`) already computed.

### 2. `tests/test_analysis.py` — update the existing test helper and 3 tests

`make_engine_state`'s signature changes to accept the new fields instead of the removed ones:
```python
def make_engine_state(
    *, current_phase=Phase.CUT,
    weight_goal_stable=False, body_fat_goal_configured=False, body_fat_goal_stable=False,
    rhr_down=False, sleep_ok=False, no_rpe_creep=False,
    bw_stable_2wk=False, strength_bounce=False, subjective_ok=False,
) -> EngineStateInput:
    return EngineStateInput(
        current_phase=current_phase,
        weight_goal_stable=weight_goal_stable,
        body_fat_goal_configured=body_fat_goal_configured,
        body_fat_goal_stable=body_fat_goal_stable,
        rhr_down=rhr_down, sleep_ok=sleep_ok, no_rpe_creep=no_rpe_creep,
        bw_stable_2wk=bw_stable_2wk, strength_bounce=strength_bounce,
        subjective_ok=subjective_ok,
    )
```
Update the 3 existing tests to construct via the new shape (e.g. `test_cut_to_stab_gate_met` becomes `weight_goal_stable=True` instead of `bodyweight=214.0, cut_to_stab_target=213.0, cut_to_stab_tolerance=2.0`; `test_cut_to_stab_gate_not_met_when_too_heavy` becomes `weight_goal_stable=False`; `test_cut_to_stab_gate_none_bodyweight_is_not_satisfied` becomes the same `weight_goal_stable=False, body_fat_goal_configured=False` default-everything-false case — rename if the old name no longer fits, e.g. `test_cut_to_stab_gate_neither_goal_configured_is_not_satisfied`). Preserve the same assertions (`phase_transition_available == Phase.STAB` / `is None`), just via the new inputs.

### 3. `ironlog/persistence/run_analysis.py` — wiring

Where `EngineStateInput(...)` is currently constructed (search for it — it also sets the STAB→REBUILD booleans from the recovery/readiness batch):
- Query `GoalSettings` singleton (`db.exec(select(GoalSettings).where(GoalSettings.id == 1)).one_or_none()`).
- **If no `GoalSettings` row exists yet**: fall back to the historical hardcoded values (`target_bodyweight=213.0, target_bodyweight_tolerance=2.0`, no body-fat goal) — this preserves the exact pre-existing behavior on day one, matching the design doc's "zero behavior change on deploy" decision, implemented as a code-level fallback rather than a migration-time data seed (this codebase's migrations never seed data — confirmed, no existing migration file contains an INSERT).
- Reuse the same trailing `DailyReadiness` rows query already built for the STAB→REBUILD signals (do not add a second, separate query for the same table).
- Call `compute_goal_stable(readiness_inputs, as_of=today, field="bodyweight", target=goals.target_bodyweight, tolerance=goals.target_bodyweight_tolerance)` → `weight_goal_stable`.
- If `goals.target_body_fat_pct is not None`: call `compute_goal_stable(readiness_inputs, as_of=today, field="body_fat_pct", target=goals.target_body_fat_pct, tolerance=goals.target_body_fat_pct_tolerance or 0.0)` → `body_fat_goal_stable`; set `body_fat_goal_configured=True`. Otherwise both stay at their `False` defaults.
- Pass all three into `EngineStateInput(...)`.

## Edge cases
- **No `GoalSettings` row yet** — must NOT crash and must NOT silently disable the gate; falls back to `213.0`/`2.0` exactly as today's hardcoded defaults did. Write a regression test for this specific case (no row in DB → gate still evaluates using the fallback values).
- **`target_body_fat_pct` set but `target_body_fat_pct_tolerance` is `None`** — default to `0.0` tolerance rather than crashing on an arithmetic error, per the fallback above (`goals.target_body_fat_pct_tolerance or 0.0`).
- **Both goals configured, only body-fat clears** — the OR must fire `Phase.STAB` (this is the athlete's stated reason for wanting body-fat as an alternative, not just weight — write a test proving this exact scenario, since it's the whole point of this spec).
- **Neither goal's stability check passes** — gate stays closed (`None`), same as today's "not met" behavior.
- Do not touch the STAB→REBUILD gate branch or its six booleans — untouched by this spec.

## Dependencies
Depends on spec 30 (`GoalSettings` model) AND spec 31 (`compute_goal_stable`) both merged first.

## Verification
- Updated `tests/test_analysis.py` tests pass with the new `EngineStateInput` shape.
- New `tests/test_run_analysis_phase_gate.py` integration tests: (a) no `GoalSettings` row → gate uses the 213.0/2.0 fallback and behaves identically to pre-this-spec behavior for the same seeded data; (b) a configured weight goal that clears (stable per spec 31's rules) → `Phase.STAB`; (c) a configured body-fat goal (with no weight goal cleared) that clears → `Phase.STAB` (the OR-fires-on-body-fat-alone case); (d) neither clears → `None`.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 586 passing, or higher if specs 30/31 already merged in this batch — check `main`'s actual count before dispatching).

## Human gate note
This spec touches a stated invariant (the phase-transition gate logic itself, not just its inputs) — route through Opus review unconditionally regardless of how clean the diff looks, same standard this codebase already applies to its other engine-invariant-touching specs (e.g. spec 27's Withings sync logic, spec 22's readiness compute functions). Not itself a schema change or new public surface, so no HUMAN GATE pause is required at merge, but do not classify this spec as review-exempt under any circumstance.
