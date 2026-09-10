## Objective
Add the schema this advancement design needs — new columns, two new tables, and one enum fix — as a single migration + SQLModel change, locked first so every dependent spec builds against a stable shape.

## Context
Source: `docs/superpowers/specs/2026-09-04-microcycle-mesocycle-advancement-design.md`, Revision 9 (approved). Current state confirmed via codebase survey 2026-09-05:
- `ironlog/models/periodization.py` has `Macrocycle`, `Mesocycle`, `Microcycle`, `MesocycleTemplate`, `BodyCompState`, `RecoveryStatus`, `DeloadState`, and a `PlanStatus` enum (`PLANNED, ACTIVE, COMPLETE, ABANDONED`) shared by `Macrocycle.status`/`Mesocycle.status`.
- `Microcycle.lifecycle_status` uses `MicrocycleLifecycleStatus` (`NOT_STARTED, ACTIVE, COMPLETE, ABORTED`) — the design's terminal value is `INCOMPLETE`, not `ABORTED`.
- `Microcycle.drift_status` uses `MicrocycleDriftStatus` (`ON_TIME, EXTENDED, DRIFT_FLAGGED`) — this already matches the design's `schedule_state` exactly; no rename needed, just note the correspondence in a docstring/comment.
- `ironlog/models/session.py`'s `Session` (class name in code: check whether it's `Session` or `WorkoutSession` — the generation code constructs `WorkoutSession(...)`, confirm the actual SQLModel class name before editing) has no `microcycle_id` or `plan_status`.
- No `MicrocycleSlot` model exists anywhere.
- No `AdvancementLog` model exists anywhere.
- `Mesocycle` has no `program_id` or `program_prescription_hash`.
- `Microcycle` has no `slot_topology_hash`.
- `Macrocycle` has no `planning_state` (distinct from `Macrocycle.status`, which is the `PlanStatus` enum — `planning_state` per the design is its own field: `ACTIVE | AWAITING_NEXT_MESOCYCLE | COMPLETE`).
- Latest migration is `deploy/migrations/067_periodization_schema.sql`; this spec is `068`.
- `MesoRotation` (in `ironlog/models/program.py`) already has a `mesocycle_id` FK — useful precedent for FK style/naming.

## File targets
- `ironlog/models/periodization.py` — add `MicrocycleSlot` model, `AdvancementLog` model, new fields on `Macrocycle`/`Mesocycle`/`Microcycle`, new enums (`MicrocyclePlanStatus`... actually reuse names below), fix `MicrocycleLifecycleStatus`.
- `ironlog/models/session.py` (or wherever the `Session`/`WorkoutSession` SQLModel class actually lives — confirm exact filename/class name first) — add `microcycle_id`, `plan_status`.
- `ironlog/models/enums.py` — add a `SessionPlanStatus` enum (`PLANNED | UNPLANNED | LEGACY`) here if that's where session-level enums conventionally live (matches `SessionStatus`'s location); otherwise co-locate with `Session`.
- `deploy/migrations/068_advancement_schema.sql` (new).
- `deploy/migrations/README.md` — follow existing migration conventions (single-statement/idempotent per house convention).

## Changes

### `MicrocycleSlot` (new model, design §2)
```
id (PK)
microcycle_id (FK -> Microcycle, required)
ordinal (int)
day_code (str, e.g. "D1".."D7")
day_label (str, display only)
planned_date (date)
slot_type (enum: TRAINING | REST)
resolution (enum: PENDING | COMPLETED | SKIPPED | NOT_APPLICABLE, default PENDING)
resolution_source (enum, nullable: SESSION | INFERRED_BOUNDARY | USER_EXPLICIT)
session_id (FK -> Session/WorkoutSession, nullable)
resolved_at (datetime, nullable)
```
Constraints: `UNIQUE(microcycle_id, ordinal)`, `UNIQUE(microcycle_id, day_code)`, `UNIQUE(session_id)` where non-null.

### `AdvancementLog` (new model, design §7)
```
id (PK)
reconcile_run_id (str/uuid, nullable — null for events logged outside a fixed-point loop, e.g. SUCCESSOR_PLANNED, PROGRAM_DRIFT_ACKNOWLEDGED)
entity_type (str: "microcycle" | "mesocycle" | "macrocycle")
entity_id (int)
reason (str: ALL_SESSIONS_RESOLVED | DRIFT_INFERRED_SKIP | MESOCYCLE_ADVANCED | PLAN_EXHAUSTED | SUCCESSOR_PLANNED | PROGRAM_DRIFT_ACKNOWLEDGED — extensible, don't hard-enum if the model uses a plain str column)
details_json (JSON, nullable — free-form: skipped_day_codes, drift_days, old/new hash, etc.)
created_at (datetime, default now)
```

### `Macrocycle`
Add `planning_state` (enum `MacroPlanningState`: `ACTIVE | AWAITING_NEXT_MESOCYCLE | COMPLETE`). This is separate from the existing `status: PlanStatus` field — do not conflate them. Backfill existing row(s): set `planning_state = ACTIVE` (the live Macrocycle currently has an active Mesocycle).

### `Mesocycle`
Add `program_id` (FK -> Program, nullable at the DB level but required by application logic going forward — nullable only so the migration doesn't fail against any pre-existing row; backfill the live Mesocycle's `program_id` to whatever `Program` its Microcycle #1 is actually using, found by inspecting the live `ironlog.db`, not guessed). Add `program_prescription_hash` (str, nullable — populated by the bootstrap spec, not this one).

### `Microcycle`
Add `slot_topology_hash` (str, nullable — populated by bootstrap/activation, not this one).
Fix `MicrocycleLifecycleStatus`: change `ABORTED` to `INCOMPLETE` (rename the enum value, not add a fifth value) — confirm via `grep -rn "ABORTED"` that no live row or code path currently depends on the literal string `ABORTED` before renaming; if any does, add `INCOMPLETE` as a new value instead and leave `ABORTED` in place unused, noting this deviation in the merge commit.

### `Session` / `WorkoutSession`
Add `microcycle_id` (FK -> Microcycle, nullable, indexed). Add `plan_status` (enum `SessionPlanStatus`: `PLANNED | UNPLANNED | LEGACY`, NOT NULL, no default — every existing row must be backfilled in the same migration to `LEGACY` since none of them predate this migration in a classified way; this spec backfills to `LEGACY` for ALL pre-existing rows, and the later bootstrap spec re-classifies the handful of post-cutover ones to `PLANNED` where mappable). `ON DELETE RESTRICT` (not CASCADE) for `Session.microcycle_id` FK, and for `MicrocycleSlot.session_id` FK back-reference if the DB enforces it both directions — per design §1's explicit restriction.

## Edge cases
- The existing single `Macrocycle`/`Mesocycle`/`Microcycle` rows from the 2026-09-04 cutover must survive this migration with sensible values, not nulls that later code treats as "unset" ambiguously. Backfill deliberately (see above), don't leave `planning_state`/`program_id` null on the live rows.
- SQLite (confirm this is still SQLite per `ironlog.db`) has limited `ALTER TABLE` support for renaming enum-backed columns' allowed values if they're stored as CHECK constraints or plain strings — if `MicrocycleLifecycleStatus` is a plain string column, renaming the Python enum value doesn't require a DDL change at all (just update any stored `'ABORTED'` string via `UPDATE`, if any exist — there shouldn't be any live rows using it).
- `Session.plan_status` NOT NULL with no default means every historical row needs a value in the same transaction as adding the column — do this as three migration steps if SQLite requires it (add nullable column, backfill, then a follow-up migration enforces NOT NULL — check whether SQLite even supports adding a NOT NULL column without a default in one step; if not, document the two-step approach in this same spec's migration file, don't split into a second spec).

## Dependencies
None — this is the foundational schema spec every other advancement spec builds on.

## Verification
- `pytest -q` (must stay green — this migration must not break any existing test relying on current `Session`/`Microcycle`/`Mesocycle` shapes).
- `tests/test_migrations.py::test_chain_matches_create_all` (or equivalent DDL/model-parity test already in the suite) passes against the new migration.
- Manual: after migration, query the live cutover's Macrocycle/Mesocycle/Microcycle rows and confirm `planning_state='ACTIVE'`, `program_id` correctly set, and no unexpected NULLs on required fields.
- A new regression test asserting `Session.plan_status` is NOT NULL and every pre-migration row backfilled to `LEGACY`.
