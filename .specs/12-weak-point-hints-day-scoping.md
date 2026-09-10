# Spec 12: Fix `build_weak_point_hints` to read `MovementState` day-scoped

## Objective
`ironlog/generation/context.py`'s `build_weak_point_hints(db)` queries **every** `MovementState` row unfiltered and keys its result dict by `movement_id` alone:
```python
states = db.exec(select(MovementState)).all()
for st in states:
    ...
    records[st.movement_id] = {...}
```
But `MovementState` has a genuine composite key: `UniqueConstraint("movement_id", "day_id")` (`ironlog/models/library.py`). A movement that appears on multiple day-tracks (e.g. Hip Thrust on D2/D5/D6, confirmed via the existing "Thrust D2/D5/D6, Reverse Hyper, Nordic, Cable Tib" per-day-state comment already in this codebase) genuinely has **one row per day**. The current code's last-write-wins-by-iteration-order dict assignment means whichever row the DB query happens to return last for that `movement_id` silently determines the stall signal used for **every** day's generation — not the row for the day actually being generated. This is a soft-signal-only bug (feeds `slot_has_deviation_signal`'s L1 hint, never a load/write path — Option-C is unaffected), but it can make a real stall on one day's track invisible during generation, or (less likely but possible) leak a different day's stall signal into a day where it doesn't apply.

## Background — the resolution pattern already exists, reuse it read-only
`ironlog/persistence/run_analysis.py`'s `_resolve_movement_state(db, movement_id, day_id)` already implements the correct priority for this same composite key, for the write path: **exact `(movement_id, day_id)` match first; else adopt/fall back to a legacy row with `day_id IS NULL`** (pre-Task-1 rows and test fixtures that predate day-scoping — every such row has exactly one entry per `movement_id`, `day_id` still at its `None` default). `ironlog/generation/loop.py:108` shows `day_id = assembled.session.day_role` — **`day_id` IS the `day_role` string** (e.g. `"D2 Lower A"`), not a separate id column; `build_context` (the caller of `build_weak_point_hints`, in `context.py`) already has `day_role` in scope (used a few lines earlier for `_recent_same_role_sessions(db, day_role)`).

**Do NOT reuse `_resolve_movement_state` itself** — it get-or-creates and stamps/mutates a legacy row's `day_id` as a side effect (correct for its write-path job during `commit_session`, wrong for a read-only hints builder that must never mutate state). Instead, implement the equivalent **read-only** priority logic directly in `build_weak_point_hints`.

## The fix
1. Change `build_weak_point_hints`'s signature to `build_weak_point_hints(db: Session, day_id: str) -> Dict[int, dict]`.
2. Query only the rows that could possibly apply to this day: `select(MovementState).where(col(MovementState.day_id).in_([day_id, None]))` — or `.where((MovementState.day_id == day_id) | col(MovementState.day_id).is_(None))`, whichever matches this file's existing query style (check imports for `col`/`or_` already used elsewhere in `context.py`).
3. Group the fetched rows by `movement_id`. For each `movement_id`, resolve to exactly one row using this priority: **an exact `day_id == day_id` row if one exists; else a `day_id IS NULL` row if one exists; else skip this movement_id entirely** (no state for this movement on this day track yet — do not fabricate one, do not raise).
4. Feed only the resolved one-row-per-movement set into the existing stall-detection loop (`detect_stall`, `select_progress_window`, etc.) — the body of the loop itself does not need to change, only which rows it iterates.
5. Update `build_context`'s call site (`context.py`, currently `weak_hints = build_weak_point_hints(db)`) to `weak_hints = build_weak_point_hints(db, day_role)` — `day_role` is already in scope at that call site, confirm this before assuming (read the function first).

## File targets
- Modify: `ironlog/generation/context.py` (`build_weak_point_hints` signature + query + resolution logic; its call site in `build_context`)
- Modify/new test(s): `tests/` — a test asserting that when a movement has TWO `MovementState` rows for different `day_id`s with different `consecutive_failed_progressions`/stall-triggering data, `build_weak_point_hints(db, "D2 Lower A")` returns the D2 row's signal and `build_weak_point_hints(db, "D5 Lower B")` returns the D5 row's signal (NOT whichever was inserted/queried last) — this is the exact regression this spec closes, so the test must actually exercise the multi-day-row case, not just a single-row happy path. Also test the legacy-fallback case: a movement with only a `day_id IS NULL` row still gets its signal returned for any day (backward-compat for pre-Task-1 fixtures/rows).

## Edge cases
- **A movement with no `MovementState` row at all for this day (neither exact nor legacy-null) is simply absent from the returned dict** — same as today's "no stall for this movement" case, not an error.
- **Do not mutate any row** — this function is read-only; no `day_id` stamping, no `db.add`/`db.flush`/`db.commit`. That mutation belongs exclusively to `_resolve_movement_state` in the write path.
- **This is a soft-signal (L1 hint) fix only** — confirm no load-writing code path is touched. `commit_session`/`run_analysis`'s Option-C two-writer boundary must remain completely untouched by this diff.
- **Existing single-day-row movements must see zero behavior change** — the vast majority of movements likely have exactly one `MovementState` row (one day, or a legacy null row); this fix must not alter their existing (correct) hint output.

## Dependencies
None — pure logic fix, no schema change, no HUMAN GATE required (no migration, no DB schema touch — `MovementState`'s `day_id` column and its unique constraint already exist).

## Verification
- New test(s) described above, green.
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` (baseline currently 506 passing).
- Manual (optional but recommended): if Hip Thrust or another multi-day movement currently has divergent `consecutive_failed_progressions` across its D2/D5/D6 rows in the live DB, spot-check `build_weak_point_hints`'s output before/after against each day_role to confirm the fix actually changes behavior for a real case (not just synthetic test fixtures) — Tier A can do this via a read-only DB query, not something the dispatched worker needs to do against production.
