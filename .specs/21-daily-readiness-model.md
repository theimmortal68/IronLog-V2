# Spec 21: `DailyReadiness` model + migration

## Objective
Add the `DailyReadiness` log table (one row per calendar day: bodyweight, resting HR, sleep-ok, subjective-ok, each with a `_source` field) that the recovery/readiness check-in design depends on — see `docs/superpowers/specs/2026-07-18-recovery-readiness-checkin-design.md` §1 for the full rationale (read it first).

## File targets
- New: `ironlog/models/readiness.py` (or add to `ironlog/models/library.py` if that's the more consistent home for a new SQLModel table in this codebase — check how `BandPair`/`MovementState` are organized and follow the same file-placement convention; do not create a third pattern).
- New migration: `deploy/migrations/029_daily_readiness.sql`
- Modify: wherever this repo's models `__init__.py`/aggregator re-exports table models (if one exists — check `ironlog/models/__init__.py`) so `DailyReadiness` is importable the same way other models are.
- New tests: `tests/test_daily_readiness_model.py` (or fold into an existing model-schema test file if this repo has one — check for a `test_library_seed.py`-style pattern first).

## The fix
```python
class DailyReadiness(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date = Field(index=True, unique=True)
    bodyweight: Optional[float] = None
    bodyweight_source: str = "manual"
    resting_hr: Optional[float] = None
    resting_hr_source: str = "manual"
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```
`date` is unique — one row per day, upserted in place by later specs, never duplicated. All the value fields are nullable (a day can be partially filled, e.g. no resting-HR device yet). The `_source` fields are the wearable-integration seam (future Health Connect/Polar sync writes rows with a different source value) — do not build any device-sync logic here, just the field.

Migration: `deploy/migrations/029_daily_readiness.sql` — a single `CREATE TABLE IF NOT EXISTS` statement (purely-additive schema, single-statement, per `deploy/migrations/README.md`'s authoring rule). Match the exact column set/types SQLModel would generate — check an existing recent migration (e.g. `028_slot_override_order.sql`) for the exact SQL style/conventions this repo uses (quoting, type names, index syntax) and mirror it.

## Edge cases
- **`date` uniqueness is load-bearing** — later specs (22/23 and the client) depend on "one row per day" to make same-day resubmission a clean upsert rather than a duplicate-row bug. Get the `unique=True` right at the model+migration level, don't defer it to application-level checking.
- Do not backfill any historical rows — this is a schema-only migration, no data migration.
- Do not touch `EngineState`'s existing six boolean columns (`rhr_down`, `sleep_ok`, `no_rpe_creep`, `bw_stable_2wk`, `strength_bounce`, `subjective_ok` in `ironlog/models/library.py`) — those are a separate, later concern (spec 23 wires real values into the pure `EngineStateInput` dataclass at analysis time; it does not touch the stored `EngineState` columns, which the design doc's investigation found are never actually read into that path today).

## Dependencies
None — standalone.

## Verification
- New test(s) confirming the model round-trips correctly and the `date` uniqueness constraint is enforced (inserting two rows with the same date should fail/conflict, not silently duplicate).
- Full server suite green: `.venv/bin/pytest -q` on myflix (check current baseline count before this change).
- Migration applies cleanly against a fresh DB copy (test via `ironlog/migrate.py`'s normal path, not a live-DB run — this spec does not touch production).

## Human gate note
This spec introduces a new DB table — a schema change, on CLAUDE.md's "Forbidden Without Pause" list regardless of the additive-schema authoring carve-out (that carve-out is about migration-file atomicity, not about bypassing the orchestration-level pause). **This worktree must not be merged without explicit human approval at the merge step**, even though the migration itself is a safe, purely-additive, single-statement change.
