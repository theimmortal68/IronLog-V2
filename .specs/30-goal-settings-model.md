# Spec 30: GoalSettings model + migration

## Objective
Add the singleton `GoalSettings` table that will hold the athlete's real, settable weight and body-fat % phase-gate goals, per `docs/superpowers/specs/2026-07-19-goal-driven-phase-gate-thresholds-design.md` (read it first).

## File targets
- Modify: `ironlog/models/library.py` — add `GoalSettings`, placed after `WithingsCredentials` (mirror `EngineState`/`WithingsCredentials`'s singleton `id: Optional[int] = Field(default=1, primary_key=True)` pattern exactly).
- Modify: `ironlog/models/__init__.py` — add `GoalSettings` to the `.library` import list.
- New: `deploy/migrations/033_goal_settings.sql`.
- New tests: `tests/test_goal_settings_model.py`.

## The fix
```python
class GoalSettings(SQLModel, table=True):
    """Singleton (id==1) holding athlete-settable phase-gate goals.
    No row is seeded by migration -- this codebase's established
    convention is that migrations never seed data (checked: no
    existing migration file contains an INSERT), so the historical
    cut_to_stab_target=213.0/tolerance=2.0 defaults are preserved via
    a code-level fallback in the READING code path (a later spec),
    not a migration-time row insert. This table starts empty until
    either that fallback or an explicit POST /goals call creates the
    row."""
    id: Optional[int] = Field(default=1, primary_key=True)
    target_bodyweight: float
    target_bodyweight_tolerance: float
    target_body_fat_pct: Optional[float] = None
    target_body_fat_pct_tolerance: Optional[float] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

Migration (single-statement, additive, no data — matches this codebase's established migration convention of schema-only migrations):
```sql
CREATE TABLE IF NOT EXISTS goalsettings (
    id INTEGER NOT NULL,
    target_bodyweight FLOAT NOT NULL,
    target_bodyweight_tolerance FLOAT NOT NULL,
    target_body_fat_pct FLOAT,
    target_body_fat_pct_tolerance FLOAT,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
```

## Edge cases
- No row exists until a later spec's read-path fallback or `POST /goals` creates one — that's expected and correct, not a bug in this spec. Do not add any seeding logic here.
- `target_bodyweight`/`target_bodyweight_tolerance` are NOT nullable at the model level (a row, once it exists, always has real weight-goal values) — only the body-fat fields are optional.
- Do not touch `EngineState`, `DailyReadiness`, `WithingsCredentials`, or any other existing model in this file.

## Dependencies
None — standalone, no shared files with spec 31 (parallel-safe).

## Verification
- New tests: round-trip all fields including both body-fat fields as `None`; confirm `id` defaults to 1 (singleton pattern).
- Migration/model parity: `tests/test_migrations.py::test_chain_matches_create_all` green.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 586 passing).
