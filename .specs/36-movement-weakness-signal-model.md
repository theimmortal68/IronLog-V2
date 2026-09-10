# Spec 36: MovementWeaknessSignal model + migration

## Objective
Add the `MovementWeaknessSignal` table that stores per-movement weak-point classification, computed after each session, per the design doc §Components 2.

## File targets
- Modify: `ironlog/models/library.py` — add `MovementWeaknessSignal` (append-only history table, NOT a singleton — unlike `EngineState`/`WithingsCredentials`/`GoalSettings`, this gets a new row per computation, so no `id=1` default). Place it near `MovementState`/`E1rmHistory` (the other movement-scoped state/history tables in this file) rather than near the singleton tables.
- Modify: `ironlog/models/__init__.py` — add `MovementWeaknessSignal` to the `.library` import list.
- New: `deploy/migrations/034_movement_weakness_signal.sql`.
- New tests: `tests/test_movement_weakness_signal_model.py`.

## The fix
```python
class MovementWeaknessSignal(SQLModel, table=True):
    """One row per movement per computation (append-only history, not
    a singleton -- the athlete's weak points change over time and a
    trend view is plausible future value, even though this batch only
    surfaces the latest state)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    session_id: int = Field(foreign_key="session.id")
    computed_at: datetime = Field(default_factory=datetime.utcnow)
    stalled: bool
    growth_rate: Optional[float] = None
    lagging: bool
    is_weak: bool
```

Migration (single-statement, additive):
```sql
CREATE TABLE IF NOT EXISTS movementweaknesssignal (
    id INTEGER NOT NULL,
    movement_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    computed_at DATETIME NOT NULL,
    stalled BOOLEAN NOT NULL,
    growth_rate FLOAT,
    lagging BOOLEAN NOT NULL,
    is_weak BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(movement_id) REFERENCES movement (id),
    FOREIGN KEY(session_id) REFERENCES session (id)
);
CREATE INDEX IF NOT EXISTS ix_movementweaknesssignal_movement_id ON movementweaknesssignal (movement_id);
```
(Check the exact FK/index DDL SQLite emits for other `foreign_key=`/`index=True` fields already in this file — e.g. look at how `MovementState.movement_id` or `E1rmHistory.movement_id` are expressed in an existing migration file — and mirror that exact style/column-type conventions rather than guessing.)

## Edge cases
- No row is written until a session is analyzed with this feature's wiring live (a separate later spec) — this spec is model/migration only, no writer, no reader.
- `growth_rate` is nullable (a movement with insufficient e1RM history for the rate calculation still gets a row, with `growth_rate=None` and `lagging=False`, but `stalled`/`is_weak` still meaningful from the stall signal alone).
- Do not touch `Movement`, `MovementState`, `E1rmHistory`, or any other existing model in this file.

## Dependencies
None — standalone, no shared files with spec 35 (parallel-safe; different files entirely).

## Verification
- New tests: round-trip all fields including `growth_rate=None`; confirm multiple rows for the same `movement_id` are allowed (NOT a singleton, unlike this batch's sibling tables) — insert two rows for the same movement at different `computed_at` timestamps and confirm both persist.
- Migration/model parity: `tests/test_migrations.py::test_chain_matches_create_all` green.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 606 passing).
