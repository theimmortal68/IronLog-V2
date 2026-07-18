# Spec 25: DailyReadiness.body_fat_pct field + migration

## Objective
Add a `body_fat_pct` field to the existing `DailyReadiness` model (spec 21, live on main), per the design doc §Components 2. Pure data capture — no gate logic, no consumer beyond storage in this spec.

## File targets
- Modify: `ironlog/models/library.py` — add `body_fat_pct: Optional[float] = None` to the existing `DailyReadiness` class (currently at line ~146-153; confirm exact location before editing, it may have shifted).
- New: `deploy/migrations/032_daily_readiness_body_fat.sql`.
- New tests: extend `tests/test_daily_readiness_model.py` (spec 21's existing test file) with a case covering the new field, rather than creating a new test file.

## The fix
Add one field to `DailyReadiness`:
```python
body_fat_pct: Optional[float] = None
```
Placed after `bodyweight`/`bodyweight_source` for readability, before `resting_hr`. **No `body_fat_pct_source` field** — unlike `bodyweight`/`resting_hr`, only the Withings integration (spec 27) will ever populate this field; there's no other write path and therefore no provenance ambiguity to track. Do not add a source field "for consistency" — that would be speculative, unused scope.

Migration (single-statement, additive):
```sql
ALTER TABLE dailyreadiness ADD COLUMN body_fat_pct FLOAT;
```

## Edge cases
- `body_fat_pct` is nullable and has no default beyond `None` — existing rows (created before this migration) get `NULL`, which is correct (no backfill, no assumed value).
- Do not touch `bodyweight`, `bodyweight_source`, `resting_hr`, `resting_hr_source`, `sleep_ok`, `subjective_ok`, or any other existing `DailyReadiness` field.
- Do not add any gate-logic consumer of this field anywhere (`ironlog/engine/readiness.py`, `run_analysis.py`) — explicitly out of scope per the design doc.

## Dependencies
Depends on spec 24 merged first (resequenced by `/verify-plan`: both specs modify `ironlog/models/library.py` — spec 24 adds `WithingsCredentials`, this spec adds one field to `DailyReadiness`. Same-file edits from two independently-generated worktrees is a real collision risk even though the changes are logically disjoint — dispatch this worktree only after spec 24 has merged, off the updated `main`).

## Verification
- Extended test: round-trip a `DailyReadiness` row with `body_fat_pct` set and one with it `None` (matching the existing nullable-field test pattern in `test_daily_readiness_model.py`).
- Migration/model parity: `tests/test_migrations.py::test_chain_matches_create_all` green.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (baseline: 563 passing, or 563+N if spec 24 merged first in this batch — check `main`'s actual count before dispatching).
