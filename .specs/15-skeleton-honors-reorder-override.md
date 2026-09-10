# Spec 15: lay_skeleton honors an active REORDER override's effective order

## Objective
`ironlog/generation/skeleton.py`'s `lay_skeleton` currently sorts a tier's exercises via `.order_by(TierExercise.exercise_order)` — a raw SQL sort on the base program table. Teach it to use each slot's *effective* order (an active `REORDER` `SlotMovementOverride`'s `override_order` if present, else the base `TierExercise.exercise_order`) — mirroring exactly how an active `MOVEMENT` override already takes precedence over the base movement without mutating the base program.

## Background
Depends on spec 14 (`OverrideType.REORDER` + `SlotMovementOverride.override_order` must exist first). Read `lay_skeleton` in full, and specifically how it currently resolves an active `MOVEMENT` override for a `TierExercise` (the precedent pattern this spec mirrors) before writing this change — do not assume the exact mechanism without reading it.

## The fix
1. Where `lay_skeleton` currently does `.order_by(TierExercise.exercise_order)` (or fetches then sorts by that column), change it to: fetch the tier's `TierExercise` rows (unsorted, or however they're currently fetched), then compute each row's effective order as `active_reorder_override.override_order if <an active REORDER override exists for this tier_exercise_id> else base.exercise_order`, then sort the in-memory list by that computed value in Python (a raw SQL `ORDER BY` can't reference a per-row override lookup without a join, and a join here would complicate the existing MOVEMENT-override-resolution code path — match whatever pattern that existing code already uses to look up an active override per `TierExercise`, likely a dict/map built once per tier rather than a query per slot).
2. Do not touch how `MOVEMENT`/`LOAD`/`REPS` overrides are resolved elsewhere in this file — this spec only changes the ordering step.

## File targets
- Modify: `ironlog/generation/skeleton.py` (`lay_skeleton`'s exercise-ordering logic)
- New/modify test(s) under `tests/`: a test asserting that when a `TierExercise` has an active REORDER override with `override_order` between two siblings' `exercise_order` values, `lay_skeleton`'s resulting skeleton reflects the new relative position; a test confirming a tier with NO active REORDER overrides produces the exact same order as before this change (regression guard).

## Edge cases
- **A tier with zero REORDER overrides must see byte-identical output to before this change** — this is the primary regression risk (an ordering-logic change that subtly reorders even the common, override-free case).
- **Multiple active REORDER overrides in the same tier** (e.g. two different notes each reordering a different slot in the same tier) — confirm the effective-order computation handles this correctly (each slot independently resolves its own override or falls back to base, then the whole set sorts together); do not assume only one override is ever active per tier.
- **An inactive (reverted, `active=False`) REORDER override must be ignored** — same as how inactive MOVEMENT/LOAD/REPS overrides are already ignored elsewhere in this codebase; mirror that exact filtering.

## Dependencies
Depends on spec 14 (`.specs/14-reorder-override-type.md`) merging first — needs `OverrideType.REORDER` and `SlotMovementOverride.override_order` to exist. No new schema change of its own (pure logic), no HUMAN GATE for this spec specifically (though it can't be deployed independently of spec 14's already-gated schema change).

## Verification
- New test(s) described above, green — especially the zero-override regression guard.
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
