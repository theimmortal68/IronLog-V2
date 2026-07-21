# Spec 44: CardioLog model + migration

## Objective
Add a standalone `CardioLog` table for logging Z2 steady-state cardio sessions (neighborhood walks, treadmill), fully decoupled from `ProgramDay`/`day_role`/generation — a log-only record, not a progressed/generated movement.

## File targets
- Modify: `ironlog/models/library.py` — add `CardioLog` class.
- Modify: `ironlog/models/__init__.py` — export `CardioLog`.
- New: `deploy/migrations/036_cardio_log.sql`
- New: `tests/test_cardio_log_model.py`

## The fix

`ironlog/models/library.py` — add after the last existing model class in the file (check current end-of-file, likely after `MovementWeaknessSignal` or similar):

```python
class CardioLog(SQLModel, table=True):
    """One row per logged Z2 cardio session (neighborhood walk or treadmill).
    Fully standalone -- NOT tied to ProgramDay/day_role, no generation, no
    progression engine involvement. Multiple rows per date are legitimate
    (e.g. a walk AND a treadmill session same day)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date
    duration_minutes: int
    avg_hr: Optional[int] = None
    modality: str  # "WALK" | "TREADMILL"
    incline_pct: Optional[float] = None
    backward_walk_done: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

Check the top of `library.py` for its existing `from datetime import ...` line — if it already imports `date` alongside `datetime` (it should, other models in this file use `date` fields already, e.g. `DailyReadiness`), no import change is needed; if not, add `date` to the existing import line. Do NOT apply the `_Date`-alias workaround — this field is named `date`, which IS the exact literal-name case that workaround exists for; check `DailyReadiness`'s own `date`-named field (if it has one) for the established alias pattern used in THIS file, and mirror it exactly rather than guessing. If no sibling model in this file has hit the naming clash (i.e. none of them use a bare `Field(...)` default alongside a field literally named `date`), the clash may not actually apply here either — confirm empirically (run the test suite after adding the class) rather than assuming either way.

`ironlog/models/__init__.py` — add `CardioLog` to the `.library` import list, alongside the existing exports.

`deploy/migrations/036_cardio_log.sql` — single additive statement (or idempotent multi-statement per this repo's additive-schema carve-out):
```sql
CREATE TABLE IF NOT EXISTS cardiolog (
	id INTEGER NOT NULL,
	date DATE NOT NULL,
	duration_minutes INTEGER NOT NULL,
	avg_hr INTEGER,
	modality VARCHAR NOT NULL,
	incline_pct FLOAT,
	backward_walk_done BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);
```
Confirm the exact column type strings this matches what `create_all` would emit for the model above (the parity invariant, `tests/test_migrations.py::test_chain_matches_create_all`) — check an existing sibling migration (e.g. `035_missed_day_record.sql`) for the exact DDL type-string conventions this repo uses (e.g. whether `bool` emits `BOOLEAN` or `INTEGER`, whether `Optional[int]` needs an explicit nullable marker) and mirror that convention exactly rather than guessing.

## Edge cases
- No seed data — this is a pure additive schema change with nothing to backfill (no existing cardio logs exist anywhere).
- Multiple `CardioLog` rows may share the same `date` — this is NOT a singleton table and NOT keyed uniquely by date. Do not add a unique constraint.
- `avg_hr`/`incline_pct` are nullable (TICKR sync doesn't always happen; incline is meaningless for a `WALK`-modality entry) — do not make them required.

## Dependencies
None.

## Verification
- New model round-trips: create a `CardioLog` row, read it back, confirm all fields including nullable ones (`None` for `avg_hr`/`incline_pct`).
- Multi-row-same-date test: two `CardioLog` rows with identical `date`, confirm both persist independently (not a singleton pattern).
- `tests/test_migrations.py::test_chain_matches_create_all` still passes after the new migration is added (parity invariant).
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 648 passing).
