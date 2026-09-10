# Spec 05a: Finisher schema — migration, models, seed data (decomposed from Spec 05)

## Objective
Lay the schema/data foundation for EMOM finishers: the new `DayFinisher` table, the new `Movement`/`MovementState` columns for D6's duration→rope progression, and the seeded rows for all 5 non-rest-day finishers — with NO behavior yet (no engine rule, no assembler wiring, no API surface change). This is step 1 of 3 in the original Spec 05 (`05-finisher-emom.md`, now the design-reference doc — read it for full background/rationale); it was split out after Spec 05 as a single dispatch proved too large for one generation pass (two gemini timeouts + one incomplete codex partial).

## Background
This spec's design decisions are already settled by `05-finisher-emom.md` and validated once already (a prior codex attempt produced exactly this shape before being abandoned for being incomplete, not wrong) — implement precisely:
- `ProgressionMode.FINISHER = "FINISHER"` added to `ironlog/models/enums.py` (EMOM finisher state — duration/rope, not load).
- `ProgressionRule.FINISHER_DURATION_THEN_ROPE = "FINISHER_DURATION_THEN_ROPE"` added to the same file's `ProgressionRule` enum.
- `Movement.rope_ladder: Optional[list]` (JSON column) added in `ironlog/models/library.py`, next to `rep_ladder`/`position_ladder`/`assist_ladder`.
- `MovementState.duration_ladder: Optional[list]` (JSON), `current_duration_seconds: Optional[int]`, `current_rope: Optional[str]` added in the same file, next to the other ladder/rule-state fields (`active_rule`, `current_body_position`, `current_rep_target`).
- `DayFinisher(SQLModel, table=True)` new class in `ironlog/models/program.py`: `id`, `program_day_id: int` (FK `programday.id`), `movement_id: int` (FK `movement.id`), `duration_minutes: int`, `params: dict` (JSON column, default empty dict).

## File targets
- Migration: `deploy/migrations/026_finisher_schema.sql` — single additive file: `CREATE TABLE dayfinisher (...)` + `ALTER TABLE movement ADD COLUMN rope_ladder ...` + `ALTER TABLE movementstate ADD COLUMN duration_ladder ...` / `ADD COLUMN current_duration_seconds ...` / `ADD COLUMN current_rope ...`. Follow `016_progression_engine_schema.sql`'s multi-statement-additive-in-one-file pattern. Column types/nullability must match the SQLModel field definitions exactly (verified by `tests/test_migrations.py::test_chain_matches_create_all`).
- Modify: `ironlog/models/enums.py` — the two new enum members (exact names/values above).
- Modify: `ironlog/models/library.py` — the three new fields on `Movement`/`MovementState`.
- Modify: `ironlog/models/program.py` — the new `DayFinisher` class.
- Modify: `ironlog/generation/program_seed.py` — seed:
  - 5 `Movement` rows (one per non-rest day: D1 `kb_swing`, D2 `sled_push`, D4 `sandbag_load_to_utility_seat`, D5 `heavy_farmer_carry`, D6 `jump_rope` — read `docs/program/phase1-warmup-finisher-source.yaml` for exact names/values), `lift_category=LiftCategory.NONE`, `progression_mode=ProgressionMode.FINISHER`.
  - Their `MovementState` rows: the 4 fixed days get no ladder state (all three new fields `None`); D6's gets `duration_ladder=[35,40,45,50]`, `rope_ladder` set on its `Movement` row to `["quarter_lb","half_lb","one_lb"]`, `current_duration_seconds=35`, `current_rope="quarter_lb"` (from the yaml's `finisher_d6_progression` block).
  - 5 `DayFinisher` rows linking each `ProgramDay` (by `day_index` 1,2,4,5,6) to its movement, with `duration_minutes=6` and `params` holding that day's yaml fields as-is (`weight_lb`/`resistance_level`/`target_reps_per_minute`/`work_seconds_per_minute`/`rest_seconds_per_minute`/`equipment`, whichever the day's yaml block has — pass them through verbatim as a dict, do not invent a rigid shared schema across days since the parameter sets genuinely differ per day).
  - D3/D7 (rest days): no `DayFinisher` row.

## Edge cases
- Column type/default parity with the migration is mandatory — this is exactly what `test_chain_matches_create_all` checks; a mismatch (e.g. nullable vs not-null, JSON vs TEXT) fails that test even though everything else "works."
- `DayFinisher.params` must default to an empty dict (`default_factory=dict`) at the model level even though every seeded row will actually populate it — this keeps the column non-nullable-safe for any future finisher added without full params.
- Do not add a `NEEDS_INPUT`/`BLOCKED` for the exact `movement_id`/`day_index` linkage — `ProgramDay.day_index` (1=Mon...7=Sun) already maps 1:1 to the yaml's `d1`..`d7` keys; use that directly, no new mapping table needed.

## Dependencies
None — this is the first sub-spec of the finisher decomposition; 05b and 05c both depend on this merging first (they use the columns/table this spec creates).

## Verification
- New test (e.g. `tests/test_finisher_seed.py`): assert all 5 `DayFinisher` rows exist and link to the correct `ProgramDay`/`Movement`; assert D6's `MovementState` has the seeded ladder values; assert D3/D7 have no `DayFinisher` row.
- `tests/test_migrations.py::test_chain_matches_create_all` green.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q` (baseline: 486 passing as of the ramp-sets merge).
- This spec deliberately produces NO behavior change to `/generate` — no new test should assert anything about the generated session response shape (that's 05c's job).
