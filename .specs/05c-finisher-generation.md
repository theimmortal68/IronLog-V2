# Spec 05c: Wire finishers into session generation + API (decomposed from Spec 05)

## Objective
Make the assembler attach each day's `DayFinisher` block to the generated session, surface it as a new `FinisherOut` DTO on the response, and update `GenerateResponse.scope`'s documented gap now that finishers are in-app. Depends on 05a (schema) and 05b (progression rule) both merging first.

## Background
Read `05-finisher-emom.md` for full rationale. This is the last of the 3-part decomposition — schema (05a) and the progression rule (05b) are already in place; this spec is purely the generation-time wiring and API surface.

## File targets
- Modify: `ironlog/generation/assembler.py` — `assemble()`: after building the main tiers/groups (end of function, before returning `AssembledSession`), resolve this session's `program_day_id` (thread it through from wherever `assemble()`'s caller already has it — check `skeleton.py`/`loop.py` for the cleanest existing handle; do not re-derive it from `day_role` string-matching if a real FK is already available upstream) and look up its `DayFinisher` row (if any — rest days have none). If found, build the finisher output using the linked `Movement`/`MovementState` (for D6, its `current_duration_seconds`/`current_rope`; for the other 4 days, just the `DayFinisher.params` pass-through).
- Modify: `ironlog/api/schemas_capture.py` — new `FinisherOut` DTO: `exercise_name: str`, `duration_minutes: int`, `params: dict`, plus optional `current_duration_seconds: Optional[int]`/`current_rope: Optional[str]` (populated only for D6, `None` otherwise). Add `finisher: Optional[FinisherOut] = None` to `SessionDetailResponse` (and wherever else the full session shape is serialized — check both the `/generate` preview path and the `/sessions/{id}` path, matching how `unit_hint` was threaded through both in Spec 01).
- Modify: `ironlog/api/app.py` — `_serialize_session`: populate the new `finisher` field. `GenerateResponse.scope` (currently hardcoded, `app.py:131-137`): remove "finishers" from the documented gap (update the literal string — this is the exact fixture/scope-text spec 05's original background called out).
- Modify: any test asserting on `GenerateResponse.scope`'s literal text or the full `/generate`/`/sessions/{id}` response shape (**mandatory fixture-impact check** — grep `tests/` for `scope` and `GenerateResponse` before writing new tests, and update what breaks).
- New test file: `tests/test_finisher_generation.py`.

## Edge cases
- **Rest days (D3, D7)**: `finisher` must be `None` on the response, not an empty/placeholder object.
- **D6 mid-progression**: the response's `current_duration_seconds`/`current_rope` must reflect the movement's actual current `MovementState`, not the seeded baseline, once any session has been logged and analyzed (i.e., this must read live state, not re-derive from the yaml/seed).
- **The finisher block must be independent of tier/meso structure** — verify the `DayFinisher` lookup keys only off `program_day_id`, never off `Tier`/`TierExercise`/`slot_id`, so a meso swap on the main work never affects finisher generation.

## Dependencies
**Depends on 05a AND 05b merging first.** Branch this worktree from the post-05b `main`.

## Verification
- New test: generate a D1 session, assert the response carries a KB Swing finisher block with `duration_minutes=6` and the D1 yaml params (`weight_lb=30`, `target_reps_per_minute=15`) passed through correctly.
- New test: generate a D3 (rest) session, assert `finisher` is `None`.
- New test: generate a D6 session at the seeded baseline, assert `current_duration_seconds=35`, `current_rope="quarter_lb"`.
- Confirm the fixture-impact check ran: any pre-existing test on `GenerateResponse.scope`'s literal text or full response shape is updated, not left failing.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`.
- Manual: `POST /generate` for D1, D3, D6; confirm the finisher block (or its absence) matches the yaml source exactly.
