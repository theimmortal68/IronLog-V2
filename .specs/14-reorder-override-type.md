# Spec 14: Add OverrideType.REORDER + override_order to SlotMovementOverride

## Objective
`SlotMovementOverride` currently supports `MOVEMENT`/`LOAD`/`REPS` override types. Add a fourth type, `REORDER`, representing a live-state change to a slot's effective sequence position within its tier — the mechanism a note-driven "move this exercise between X and Y" request will use (via a separate, dependent resolver spec), instead of the hand-written one-off migration scripts used so far (e.g. `d4_reorder_knee_raise.py`).

## Background — why a float insertion value, not renumbering siblings
`TierExercise.exercise_order` (int, 1-based) is the base program's static ordering, read via `ironlog/generation/skeleton.py:135`'s `.order_by(TierExercise.exercise_order)`. Reordering one exercise relative to its neighbors is a request for a new *relative position*, not an isolated absolute one — renumbering every sibling's `exercise_order` would mean creating/mutating multiple rows for one note (and `SlotMovementOverride`'s existing invariant is "base program never mutated; revert = active=False" — a single override row per slot). Instead, `override_order: Optional[float]` lets the override insert strictly between two neighbors' `exercise_order` values (e.g. neighbors at 2 and 3 → `override_order = 2.5`) — one override row, one slot touched, no renumbering, no collision. This is a well-worn pattern (the same idea as Trello/Notion card positions).

## The fix
1. **`ironlog/models/enums.py`'s `OverrideType`**: add `REORDER = "REORDER"` alongside `MOVEMENT`/`LOAD`/`REPS`. Update the enum's docstring to describe it: "REORDER: adjust the slot's effective sequence position (`override_order`) without touching the base program's `exercise_order`."
2. **`ironlog/models/program.py`'s `SlotMovementOverride`**: add `override_order: Optional[float] = None`.
3. **Migration** (additive, single-statement): new file `deploy/migrations/028_slot_override_order.sql`:
   ```sql
   ALTER TABLE slotmovementoverride ADD COLUMN override_order REAL;
   ```
   Read `deploy/migrations/README.md` first and follow its single-statement-atomic rule exactly. Confirm `tests/test_migrations.py::test_chain_matches_create_all` stays green.
4. **`ironlog/notes/apply.py`'s `apply_override`**: add a `REORDER` branch. Signature gains an `override_order: Optional[float] = None` kwarg. Validation: `REORDER` requires `override_order` to be provided (raise `ValueError` if `None`, matching the existing pattern for LOAD's "exactly one of load_delta/load_absolute" check). Set `kw["override_movement_id"] = te.movement_id` (same harmless-placeholder pattern already used for LOAD, since the NOT-NULL constraint on that column applies regardless of override_type) and `kw["override_order"] = override_order`.
5. **`ironlog/api/app.py`'s `ApplyNoteRequest`**: add `override_order: Optional[float] = None`. Thread it through to `apply_override`'s call site (`apply_note`, the `/notes/{note_id}/apply` endpoint) alongside the other override-type-specific kwargs already threaded there.

## File targets
- Modify: `ironlog/models/enums.py` (`OverrideType.REORDER`)
- Modify: `ironlog/models/program.py` (`SlotMovementOverride.override_order`)
- New: `deploy/migrations/028_slot_override_order.sql`
- Modify: `ironlog/notes/apply.py` (`apply_override`'s REORDER branch)
- Modify: `ironlog/api/app.py` (`ApplyNoteRequest.override_order`, thread through `apply_note`)
- New/modify test(s) under `tests/`: a test creating a REORDER override via `apply_override` and confirming `override_order` is persisted correctly; a test confirming `apply_override(..., override_type="REORDER", override_order=None)` raises `ValueError` (missing required value, mirroring the existing LOAD validation-error test pattern if one exists — find it first).

## Edge cases
- **`override_movement_id`'s NOT NULL constraint**: REORDER, like LOAD, must still set a harmless placeholder (`te.movement_id`) — do not attempt to relax this column's nullability (per the existing documented convention: "kept NOT NULL to match the existing 021 schema — additive-only migrations, no column-nullability change").
- **Do not touch `MOVEMENT`/`LOAD`/`REPS` branches** — this is a pure addition, zero behavior change for the three existing override types.
- **Revert (`active=False`) already works generically** for any `SlotMovementOverride` row regardless of type — confirm this by reading the existing revert endpoint/logic, but no code change should be needed there; if you find it DOES need a change, flag with `NEEDS_INPUT` rather than guessing.

## Dependencies
None — standalone. **DB SCHEMA CHANGE — this diff's merge and its eventual deploy to production both require explicit human confirmation per CLAUDE.md's Forbidden-list boundary (Database schema changes) — do not assume authorization from this spec alone.**

## Verification
- New test(s) described above, green.
- `tests/test_migrations.py::test_chain_matches_create_all` green (parity keystone).
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
