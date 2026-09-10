# 05 — Read-only periodization API endpoints

## Objective
Add the two read-only endpoints from design doc §9: `GET /training/plan/current` and `GET /training/macrocycles/{id}`. Additive only — no existing endpoint or response shape changes (per repo-root CLAUDE.md's client-contract section, this needs no `IronLog-V2-Client` DTO changes since nothing existing is touched).

## File targets
- `ironlog/api/app.py` — two new route handlers, following the existing flat-router pattern (see `@app.get("/sessions/today", ...)` at line 1088 for the closest existing precedent: a "what's currently active" read endpoint)
- `ironlog/api/schemas_periodization.py` (new) — response Pydantic models, matching the naming convention of `ironlog/api/schemas_readiness.py`, `schemas_wizard.py`, etc. (one `schemas_*.py` file per feature area is this repo's existing convention — do not add these models inline in `app.py`)

## Changes

### `GET /training/plan/current`
Response model `CurrentPlanOut` (in the new schemas file): current `macrocycle` (nullable — a Mesocycle need not belong to one, per design doc §1), current `mesocycle`, current `microcycle` (`planned_posture`, `effective_posture`, `lifecycle_status`, `drift_status`, `drift_days`, planned/actual dates), current `body_comp_state`, current `recovery_status`, active `deload_state` (nullable), and a condensed `resolver_trace` (design doc §8/§9 — "at minimum a condensed form of this trace is exposed via the read API"). Reuse the exact "what's currently active" Microcycle-lookup logic spec 04 built for `GenerationContext` (do not reimplement a second, possibly-divergent version of "what is today's Microcycle" — import/call the same helper spec 04 introduced in `ironlog/generation/context.py`, or if it's not factored out as a standalone importable function, that's a signal to ask spec 04's implementer to factor it out rather than duplicating the query here). If no Microcycle/Mesocycle/Macrocycle exists yet (pre-cutover), return all fields null/empty rather than erroring — this endpoint must work in a not-yet-migrated install too.

### `GET /training/macrocycles/{id}`
Response model `MacrocycleDetailOut`: `goal`, `planned_start_date`, `planned_end_date`, `status`, ordered list of its Mesocycle instances (each with `template_id`/`template_name`, `ordinal`, planned/actual dates, `status`). 404 if the id doesn't exist (match this repo's existing 404 convention — check how `get_movement`/`get_session_detail` handle a missing id).

## Edge cases
- No auth changes — reuse whatever `Depends(get_session)`/existing auth dependency pattern every other endpoint in `app.py` already uses (this app has token auth per project memory — do not add a new auth mechanism, do not skip existing auth on these new routes).
- `GET /training/plan/current` with a Mesocycle that has no `macrocycle_id` (standalone) must return `macrocycle: null`, not error.

## Dependencies
`.specs/01-periodization-data-model.md` (schema), `.specs/03-policy-resolver.md` (trace shape), `.specs/04-generation-wiring-prescription-snapshot.md` (the current-Microcycle lookup helper this spec reuses rather than duplicates).

## Verification
- New `tests/test_periodization_api.py`: `GET /training/plan/current` with no periodization data seeded → 200, all-null response. With a full Macrocycle/Mesocycle/Microcycle fixture seeded → 200, all fields populated and matching the fixture. `GET /training/macrocycles/{id}` happy path and 404 path.
- `pytest -q` fully green.
