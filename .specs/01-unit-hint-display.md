# Spec 01: Surface a unit hint on the session API (C-display, server half)

## Objective
Surface enough information on `ExerciseOut` for a client to render an ASSISTED/incline movement's target as "20° assist" instead of "20 lb" — today the session API hands back a bare `target_load` float with no unit, so every assist/incline movement (Nordic Curl, Reverse Nordic Curl, Face-Up Incline Knee Raise) displays as a weight.

## File targets
- Modify: `ironlog/api/schemas_capture.py` — `ExerciseOut` (class at line 70-78)
- Modify: `ironlog/api/app.py` — `_serialize_session` (function at line ~519; `ExerciseOut(...)` construction at line ~554-560); reuse the existing `_UNIT_HINTS` dict (line 702) and `load_field_for_mode` (imported at line 46 from `ironlog/generation/load_trust.py`)

## Changes
1. `schemas_capture.py`: add `unit_hint: Optional[str] = None` to `ExerciseOut` (after `objective`, before `planned_sets` — field order doesn't matter functionally but keep it readable).
2. `app.py`, `_serialize_session`: at the `ExerciseOut(...)` construction (~line 554), the `Movement` row `mv` is already fetched (`mv = db.get(Movement, pe.movement_id)`, ~line 542). Compute `unit_hint=_UNIT_HINTS.get(load_field_for_mode(mv.progression_mode)) if mv else None` and pass it into the `ExerciseOut(...)` call. This mirrors the exact pattern already used for the wizard endpoint (`ironlog/api/app.py:805`, `unit_hint=_UNIT_HINTS.get(r.load_field)`) — do not invent a new mapping, reuse `_UNIT_HINTS` and `load_field_for_mode` verbatim.
3. No change to `_UNIT_HINTS` itself (`{"current_load": "lb", "assist_level": "assist"}` already covers both fields correctly).

## Edge cases
- Bodyweight movements (`load_field_for_mode` returns `None` — PROTOCOL/CONDITIONING/NONE, e.g. pull-ups, ab wheel, dragon flag): `unit_hint` must be `None`, not `"lb"` or a stringified `None`. Confirm `_UNIT_HINTS.get(None)` returns `None` (dict `.get` on a missing key with no default → `None` — verify this, it's relying on `None` not being a key in `_UNIT_HINTS`).
- The in-memory (uncommitted) generate-candidate path also calls `_serialize_session` (see its own docstring: "Also used for in-memory (uncommitted) generate candidates") — `mv` is fetched the same way in that path, so no special-casing needed; confirm the preview/candidate response also carries `unit_hint` correctly (test via `/generate`, not just `/sessions/today`).
- HT/COMPOSITE movements: `load_field_for_mode(ProgressionMode.COMPOSITE)` returns `"current_load"` → `unit_hint="lb"` — this is fine/inert since HT already carries `target_plates`/`band_config`/`target_felt_peak` on `PlannedSetOut` and the client's existing `htSetupLine` rendering doesn't consult `unit_hint` at all; do not touch HT rendering in this spec.

## Dependencies
None (standalone). This is a prerequisite for the client-side render fix (a separate spec in the `IronLog-V2-Client` repo — out of scope here; note in the routing plan that the client half cannot start until this merges and is confirmed live, since the client needs a real `unit_hint` field to key off).

## Verification
- `ssh myflix "cd ~/projects/IronLog-V2 && .venv/bin/pytest -q"` — full suite green, no regressions.
- New/extended test (e.g. in `tests/test_api_capture.py` or the nearest existing DTO/serialization test file — locate the real one via `grep -rl "_serialize_session\|ExerciseOut" tests/`): generate a D1 session, assert Bench Press's `ExerciseOut.unit_hint == "lb"`, Pull-up's `unit_hint is None`, and (generate D1/D4) Face-Up Incline Knee Raise's `unit_hint == "assist"`.
- Manual: `curl -s -X POST http://localhost:8000/generate -d '{"day_role":"D1 Upper Push"}' | python3 -m json.tool` — confirm `unit_hint` present and correct per exercise in the raw response.
