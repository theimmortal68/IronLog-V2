# 01 — Periodization data model + migration

## Objective
Add the SQLModel tables and migration for the new macrocycle/mesocycle/microcycle hierarchy and state axes, per `docs/superpowers/specs/2026-09-03-long-range-periodization-design.md` §1–8, without touching any existing runtime behavior yet (this spec is schema + models only — no resolver, no wiring, no API).

## File targets
- `ironlog/models/periodization.py` (new) — all new SQLModel tables
- `ironlog/models/__init__.py` — export the new models (match existing export pattern)
- `ironlog/models/program.py` — `MesoRotation`: add `mesocycle_id: Optional[int] = Field(default=None, foreign_key="mesocycle.id")` column (additive, nullable — do not drop `meso_number` yet, that happens in spec 02)
- `ironlog/models/session.py` — `Session`: add `prescription_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSON))` column (additive, nullable)
- `deploy/migrations/067_periodization_schema.sql` (new) — additive schema only (new tables + new nullable columns), per `deploy/migrations/README.md`'s carve-out for multi-statement purely-additive schema changes

## Changes

### New tables (in `ironlog/models/periodization.py`)
Read `docs/superpowers/specs/2026-09-03-long-range-periodization-design.md` in full first — it is the source of truth for field semantics. Model each table as a SQLModel class following this repo's existing patterns in `ironlog/models/program.py` (see `Program`, `ProgramDay`, `Tier`, `TierExercise`, `MesoRotation`, `WeekParityRotation` for conventions: `id: Optional[int] = Field(default=None, primary_key=True)`, explicit `foreign_key=` strings, enum columns via `sa_column=Column(SAEnum(...))` matching how `ironlog/models/enums.py` enums are wired elsewhere in this file).

- **`MacrocycleTemplate`** — not in the design doc as a separate template concept (only `MesocycleTemplate` is a template); Macrocycle itself is NOT templated, skip this table — a `Macrocycle` is created directly with a goal, no template layer. (Flagging so the implementer doesn't invent a table the design doc doesn't call for.)
- **`Macrocycle`** — `id`, `goal: str`, `planned_start_date: Optional[date]`, `planned_end_date: Optional[date]`, `status: str` (free enum, e.g. `PLANNED|ACTIVE|COMPLETE|ABANDONED` — pick a small explicit enum, document choice in commit message).
- **`MesocycleTemplate`** — `id`, `name: str` (unique), `postures: List[str]` stored as JSON (ordered list of `training_posture` string values — open vocabulary per design doc §3, do NOT make this an enum column, use plain strings so new posture names don't require a migration).
- **`Mesocycle`** (instance) — `id`, `template_id: int` (FK `mesocycletemplate.id`), `macrocycle_id: Optional[int]` (FK `macrocycle.id`, nullable — design doc §1: "A Mesocycle is not required to belong to a Macrocycle"), `ordinal: Optional[int]` (position within macrocycle, nullable if standalone), `planned_start_date`, `planned_end_date`, `actual_start_date: Optional[date]`, `actual_end_date: Optional[date]`, `status: str` (`PLANNED|ACTIVE|COMPLETE|ABANDONED`).
- **`Microcycle`** — `id`, `mesocycle_id: int` (FK), `ordinal: int` (position within mesocycle — **this is the field `MicrocycleParityRotation` in spec 02 keys off, must be stable and 1-indexed per mesocycle**), `planned_start_date: date`, `planned_end_date: date`, `actual_start_date: Optional[date]`, `actual_completion_date: Optional[date]`, `expected_sessions: int`, `completed_sessions: int` (default 0), `lifecycle_status: str` (`NOT_STARTED|ACTIVE|COMPLETE|ABORTED`), `drift_status: str` (`ON_TIME|EXTENDED|DRIFT_FLAGGED`, default `ON_TIME`), `drift_days: int` (default 0), `planned_posture: str` (immutable once set — enforce via no-op update guard in code, not a DB constraint), `effective_posture: str` (nullable until resolved).
- **`BodyCompState`** — timeline table: `id`, `state: str` (`CUT|MAINTENANCE|GAIN`), `effective_from: date`, `effective_to: Optional[date]` (nullable = current/open-ended), `notes: Optional[str]`.
- **`RecoveryStatus`** — computed/derived snapshot table, one row per day it's evaluated: `id`, `as_of_date: date` (unique), `status: str` (small explicit enum, e.g. `NORMAL|CAUTION|POOR` — this is the resolver's input value, not raw RHR/sleep/HRV, which stay in the existing readiness pipeline `ironlog/engine/readiness.py` reads from), `inputs_snapshot: Optional[dict]` (JSON — the raw `compute_rhr_down`/`compute_sleep_ok`/etc. booleans that produced `status`, for auditability). This table does NOT duplicate the existing readiness capture — it stores the *resolved* status derived from it (the actual resolver logic that computes `status` from those inputs is spec 03's job, not this spec's).
- **`DeloadState`** — `id`, `microcycle_id: Optional[int]` (FK, nullable until a deload is actually active/attributed to a microcycle), `active: bool` (default False), `triggered_at: Optional[date]`, `trigger_reason: Optional[str]` (free text — persistence evidence summary), `resolved_at: Optional[date]` (nullable while still active).

### `MesoRotation` extension (`ironlog/models/program.py`)
Add `mesocycle_id: Optional[int] = Field(default=None, foreign_key="mesocycle.id")`. Leave `meso_number: int` in place, unchanged, still required — spec 02 handles cutting over resolution logic to prefer `mesocycle_id`; this spec only adds the column.

### `Session` extension (`ironlog/models/session.py`)
Add `prescription_snapshot` as a nullable JSON column, per design doc §7's exact field list (store the whole dict as one JSON blob, do not create 8 new columns). Do not touch `Session.phase` — it stays exactly as-is, historical-only per design doc §7, no behavior change in this spec.

### Migration file
`deploy/migrations/067_periodization_schema.sql` — `CREATE TABLE` for `macrocycle`, `mesocycletemplate`, `mesocycle`, `microcycle`, `bodycompstate`, `recoverystatus`, `deloadstate`, plus `ALTER TABLE mesorotation ADD COLUMN mesocycle_id INTEGER REFERENCES mesocycle(id)` and `ALTER TABLE session ADD COLUMN prescription_snapshot TEXT` (SQLite JSON is stored as TEXT — match however `TierExercise`'s existing JSON columns are declared in prior migrations, e.g. check `deploy/migrations/062_timed_tier_exercise_schema.sql` for the pattern this repo already uses for JSON columns). Follow the DDL types/nullability exactly as the SQLModel classes emit them (the migration README's parity invariant — `tests/test_migrations.py::test_chain_matches_create_all` will fail otherwise). This is a single purely-additive-schema migration file (new tables + new nullable columns only, zero data changes) — the README's carve-out permits this as one multi-statement file.

## Edge cases
- `Microcycle.planned_posture` must never be written to by anything except initial creation from the mesocycle template — this spec just needs the column to exist; enforcing immutability is a later spec's job (03/04), but don't add an `onupdate` trigger or anything that would make future immutability enforcement harder.
- `RecoveryStatus.as_of_date` unique constraint — one resolved status per day, upserted, not appended indefinitely.
- Do not add a `DeloadState.microcycle_id` NOT NULL constraint — a deload can in principle be evaluated before being attributed to a specific microcycle in edge cases (defensive nullability, per design doc's persistence-based triggering logic in spec 03).

## Dependencies
None — this is the foundational spec every other spec in this batch depends on.

## Verification
- `alembic`/this repo's own `ironlog/migrate.py apply_pending` runs clean against a fresh test DB (use the existing `gen_db`/`test_db` pytest fixture pattern — check `tests/conftest.py` for how other tests spin up an ephemeral DB).
- `tests/test_migrations.py::test_chain_matches_create_all` passes with the new tables included (this test already exists and diffs live-`create_all` against the full migration chain — it will automatically catch any DDL/model mismatch).
- New file `tests/test_periodization_models.py` (new, in scope for this spec): construct one row of each new table via SQLModel, assert round-trip persistence (create, commit, re-query, assert field values) for `Macrocycle`, `MesocycleTemplate`, `Mesocycle`, `Microcycle`, `BodyCompState`, `RecoveryStatus`, `DeloadState`, plus a `MesoRotation` row with `mesocycle_id` set and a `Session` row with `prescription_snapshot` set.
- `pytest -q` fully green, no regressions to existing tests.
