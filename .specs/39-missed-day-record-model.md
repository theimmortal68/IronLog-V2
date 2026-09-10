# Spec 39: MissedDayRecord model + migration

## Objective
Add the `MissedDayRecord` table that tracks a missed scheduled training day and the athlete's action on it, per `docs/superpowers/specs/2026-07-20-missed-workout-handling-design.md` (read it first).

## File targets
- Modify: `ironlog/models/program.py` — add `MissedDayRecord`, placed after `ProgramDay` (this file's other program-definition tables — `Program`, `ProgramDay`, `DayFinisher`, `Tier` — live here; this new table references `ProgramDay` via FK so belongs in the same file, even though it's state/history rather than pure definition).
- Modify: `ironlog/models/__init__.py` — add `MissedDayRecord` to whatever import list already re-exports `ProgramDay`/`DayFinisher` from `.program` (check the exact current import style — this file's `.library` import list is well-established from prior specs, but `.program`'s might have a different existing shape, check it first).
- New: `deploy/migrations/035_missed_day_record.sql`.
- New tests: `tests/test_missed_day_record_model.py`.

## The fix
```python
class MissedDayRecord(SQLModel, table=True):
    """One row per detected missed training day (append-only history,
    NOT a singleton — mirrors MovementWeaknessSignal's pattern, not
    EngineState/GoalSettings/WithingsCredentials's id=1 singleton
    pattern). status is mutated in place as the athlete acts on it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="programday.id", index=True)
    week_start_date: date       # Monday of the missed week
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "PENDING"     # PENDING | ACKNOWLEDGED | RESCHEDULED | RESOLVED
    resolved_at: Optional[datetime] = None
```
Check this file's existing imports for `date`/`datetime` — `ironlog/models/program.py` currently imports `from datetime import datetime` only (no `date`) per its own header; you'll need to add `date` to that import (do NOT introduce the `date`-as-type-annotation-clash workaround from `ironlog/models/library.py` unless you actually hit that Pydantic error — `week_start_date` is a different field NAME than `date`, so the clash may not apply here at all; only use the alias workaround if you empirically hit the error, don't apply it preemptively).

Migration (single-statement, additive):
```sql
CREATE TABLE IF NOT EXISTS misseddayrecord (
    id INTEGER NOT NULL,
    program_day_id INTEGER NOT NULL,
    week_start_date DATE NOT NULL,
    detected_at DATETIME NOT NULL,
    status VARCHAR NOT NULL,
    resolved_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(program_day_id) REFERENCES programday (id)
);
CREATE INDEX IF NOT EXISTS ix_misseddayrecord_program_day_id ON misseddayrecord (program_day_id);
```

## Edge cases
- No row is written until the detection logic (a separate later spec) runs — this spec is model/migration only, no writer, no reader.
- `status` is a plain string, not an enum column type (mirrors this codebase's existing `EngineState.current_phase`-adjacent conventions where relevant, but check whether other status-like fields in this codebase use a Python `Enum`/`str, Enum` class instead of a bare string — if there's a clear established convention for "one of a small fixed set of string values," follow it rather than a bare `str`).
- Do not touch `Program`, `ProgramDay`, `DayFinisher`, `Tier`, `TierExercise`, `MesoRotation`, or `SlotMovementOverride` in this file — pure addition.

## Dependencies
None — standalone.

## Verification
- New tests: round-trip all fields including `resolved_at=None`; confirm multiple rows for the same `program_day_id` are allowed (not a singleton) — insert two rows for the same `program_day_id` at different `week_start_date` values and confirm both persist with distinct auto-incremented `id`s.
- Migration/model parity: `tests/test_migrations.py::test_chain_matches_create_all` green.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 624 passing).

## Human gate note
This is a DB schema change — HUMAN GATE at dispatch AND at merge, same standing pattern for every new table this session.
