# Fix 5 report — day-scope commit_session's MovementState writer

## Bug

`commit_session` in `ironlog/generation/loop.py` (the SOLE writer of
`current_load` / `ht_plates` / `ht_band_config` — Fork 7c, Option-C) looked
up `MovementState` day-blind:

```python
st = db.exec(
    select(MovementState).where(MovementState.movement_id == mid)
).first()
if st is None:
    st = MovementState(movement_id=mid)
```

`MovementState` carries a `UniqueConstraint("movement_id", "day_id")` because
movements shared across days (Hip Thrust D2/D5/D6, Reverse Hyper, Nordic,
Cable Tib) each have their own per-day row. The read path
(`_resolve_movement_state` in `ironlog/persistence/run_analysis.py`, day-scoped
in Task 5) was already correct, but this write path's `.first()` — with no
`day_id` filter — returned whichever day's row happened to be created first,
regardless of which day was actually being committed. Committing a session for
one day could silently overwrite a *different* day's advancement, corrupting
the shared HT/scalar tracks.

Confirmed empirically: for the Hip Thrust movement, `seed_movement_baselines`
creates rows in insertion order D2 → D5 → D6 (ids 11, 22, 31 in a fresh
`gen_db`). Committing a **D6** session under the buggy code updated **D2's**
row (id 11, the `.first()` result) and left D6's own row untouched.

## Fix

`ironlog/generation/loop.py`:

- Added `day_id = assembled.session.day_role` — the committing session's own
  day, exactly mirroring `run_analysis.py`'s `day_id = workout.day_role`
  (confirmed via `grep -n "day_role" ironlog/persistence/run_analysis.py` and
  `ironlog/generation/assembler.py:284` / `ironlog/models/session.py:25`,
  which is the `Session.day_role: str` field, e.g. `"D2 Lower A"`,
  `"D6 Weak Points"`).
- Replaced the day-blind lookup/create with a call to
  `_resolve_movement_state(db, mid, day_id)`, imported from
  `ironlog.persistence.run_analysis` (that module is unmodified — only
  imported from). This reuses the Task-5 get-or-create-with-legacy-adoption
  helper verbatim instead of re-implementing it:
  - exact `(movement_id, day_id)` match first;
  - else adopt a legacy (`day_id IS NULL`) row for this `movement_id` by
    stamping its `day_id` (needed because `gen_db_calibrated` and several
    other fixtures/seeds pre-date the progression engine and stamp exactly
    one `day_id=None` row per movement; a naive "miss → create new row" would
    leave those fixtures with two rows per `movement_id` and break their
    `movement_id`-only `.one()` lookups in `test_ht_write_boundary.py` and
    `test_generation_commit.py` — confirmed by trying the naive version
    first, which broke exactly those two tests with `MultipleResultsFound`);
  - else create a fresh `MovementState(movement_id=mid, day_id=day_id)`.
- Removed the now-unused `select` (sqlmodel) and `MovementState` (models)
  imports from `loop.py` (the get-or-create query moved into the reused
  helper).
- Nothing else in the two-writer boundary changed: `commit_session` remains
  the sole writer of `current_load` / `ht_plates` / `ht_band_config`;
  `run_analysis` still writes none of them (verified by
  `test_write_boundary.py` / `test_ht_write_boundary.py` staying green).

## TDD — per-day isolation

New file: `tests/test_commit_day_scoped_state.py`, two tests.

**1. `test_commit_advances_only_the_committing_days_ht_row`** (real path):
seeds HT baselines via `seed_movement_baselines` (D2=205, D5=205, D6=155, all
Orange band `[1]`), generates + commits a real **D6** session via
`lay_skeleton` → `resolve_context` → `program_selections` → `assemble` →
`commit_session`, then asserts:
- D6's own row advances to the staged-next setup `(160.0, [1])`.
- D2's row (`(205.0, [1])`) and D5's row (`(205.0, [1])`) are byte-identical
  to before the commit.
- Still exactly 3 rows for the movement (no stray row, no collapse).

Before the fix: `AssertionError: D6's own row should advance on its own
commit, got (155.0, [1])` — the D6 commit updated D2's row (lowest id) and
left D6's own row at its seeded baseline.

**2. `test_commit_advances_only_the_committing_days_scalar_row`** (handcrafted,
cheap): picks a movement with no pre-existing `MovementState` row, inserts
**"Day B" first** (lower id, `current_load=200.0`) then **"Day A"**
(`current_load=100.0`) — deliberately reproducing the exact corruption shape
(the day NOT being committed has the lower id) — then commits for `"Day A"`
with `prospective_current_loads={mv.id: 150.0}` via a handcrafted
`AssembledSession`. Asserts Day A's row becomes `150.0`, Day B's stays
`200.0`, and still exactly 2 rows.

Before the fix: `AssertionError: committing Day A should advance Day A's own
row` — `100.0 == 150.0` failed because `.first()` picked Day B's row and
overwrote it instead.

Both tests confirmed **red** before the fix (ran via
`ssh myflix "bash -lc 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_commit_day_scoped_state.py'"`),
then **green** after applying the fix, run together with the existing
guardrails:

```
ssh myflix "bash -lc 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q \
  tests/test_commit_day_scoped_state.py \
  tests/test_generation_day_scoped_state.py \
  tests/test_ht_write_boundary.py \
  tests/test_write_boundary.py \
  tests/test_generation_commit.py \
  tests/test_golive_phase1.py'"
# 14 passed
```

## Full suite

```
ssh myflix "bash -lc 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'"
# 449 passed, 983 warnings in 10.83s
```

No regressions. `test_golive_phase1.py`, both Option-C write-boundary
guardrails (`test_write_boundary.py`, `test_ht_write_boundary.py`), and the
Task 5 read-scoping test (`test_generation_day_scoped_state.py`) all still
pass unmodified.

## Files changed

- `ironlog/generation/loop.py` — day-scoped `commit_session` writer (fix).
- `tests/test_commit_day_scoped_state.py` — new (2 tests, TDD red→green).

## Constraints honored

- No `from __future__ import annotations` added.
- `run_analysis.py` and the read path untouched (only imported from).
- Option-C two-writer guardrail tests (`test_write_boundary.py`,
  `test_ht_write_boundary.py`) unmodified and still green.
