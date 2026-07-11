# Spec 06: Fix `last_valid_selections` to match by slot identity, not position

## Objective
Fix `last_valid_selections` (`ironlog/generation/fallback.py:46-99`) so it no longer silently defeats a program-structure change (e.g. an exercise reorder, or a meso-rotation swap) whenever a prior COMPLETED session exists for that day and a deviation signal routes generation through the fallback/replay path.

## Background — confirmed root cause (2026-07-10)
`last_valid_selections` zips the CURRENT skeleton's `adaptive_slots` (already correctly ordered via `TierExercise.exercise_order`, per `skeleton.py:131`) against the PRIOR completed session's exercises **by raw position** (`zip(slot_iter, non_anchor)`, fallback.py:87). If the program's slot order has changed since that prior session was logged (exactly what happened after the D4 exercise reorder, spec/commit `f91fbc5` + the live `d4_reorder_knee_raise.py` migration), this produces a **slot_id → movement_id mismatch**: the i-th CURRENT slot (now representing a different logical position than before) gets assigned the i-th PRIOR exercise's movement, silently reproducing the stale arrangement.

Confirmed live: after the D4 reorder was deployed, `/generate` for "D4 Upper Pull" still showed the OLD order (Meadows Row, Single-Arm DB Row, Face-Up Incline Knee Raise) whenever `should_invoke_llm` returned `True` (an open note was the active deviation signal for D4 at the time) — because generation routed through `fallback_session` → `last_valid_selections`, not the plain deterministic `program_selections` path. Confirming the note removed the deviation signal and restored the correct order purely as a side effect, proving the reorder itself was never the problem — the replay path was.

**This is a general latent bug, not D4-specific.** Any day whose `TierExercise.exercise_order` or meso-rotation membership changes after a COMPLETED session exists for that day will hit this same silent-mismatch behavior the next time ANY deviation signal (stall, weak-point hint, novelty-owed, open note) is active for that day.

## The fix
Replace positional zipping with matching by **slot identity + movement reachability**:

1. For each current adaptive slot (`skeleton.adaptive_slots`, already correctly ordered), build its **reachable movement set**: the slot's own base `program_movement_id` plus every `MesoRotation.movement_id` for that `TierExercise.id` (across all `meso_number` values) — these are the only movements that could ever legitimately have filled this slot.
2. From the prior session's non-anchor exercises (same extraction as today — last `len(slot_iter)` exercises, PRIMARY_NOT_FIRST invariant), build a **used-movement pool**: `{movement_id: exercise}` (a prior exercise's movement_id should appear at most once per prior session, but guard defensively if not).
3. For each current slot **in its correct order**, if the used-movement pool contains a movement_id in that slot's reachable set, assign it (removing it from the pool) — this preserves "the athlete was doing the meso-2 swap variant" continuity across regenerations, now correctly attached to the RIGHT slot regardless of order changes.
4. Any current slot left unmatched (its historical movement isn't in the prior pool, or the day's slot count/structure changed) falls back to its own `program_movement_id` (today's cold-start behavior, unchanged).
5. Any leftover prior-pool entries that matched no current slot are simply discarded (the structure changed out from under them — correct behavior, not an error).

This keeps the function's actual purpose intact (refresh loads while preserving "which movement variant was in play") while making it robust to the underlying program structure changing between the prior session and now.

## File targets
- Modify: `ironlog/generation/fallback.py` — `last_valid_selections` (lines 46-99). Likely needs a new small helper (e.g. `_reachable_movements(tier_exercise_id, db) -> Set[int]`) querying `MesoRotation` for the slot's `TierExercise`, unioned with the `TierExercise.movement_id` field itself.
- New test file or addition to an existing fallback-focused test file (check `tests/` for the current `last_valid_selections`/`fallback_session` test file naming convention and mirror it).

## Edge cases
- **No program-structure change since the prior session** (the common case): matching-by-identity must produce IDENTICAL output to the current positional behavior — this is a pure robustness fix, not a behavior change for the unchanged case. A regression test locking in "reorder-free day still replays correctly" is mandatory.
- **A slot's prior movement is a meso-rotation variant that's since been retired/removed from `MesoRotation`**: falls back to the slot's own `program_movement_id` (case 4 above) — must not crash on a movement_id no longer reachable.
- **Two different current slots could theoretically share an overlapping reachable set** (e.g. if a meso rotation ever reused a movement across two slots — verify this doesn't happen in the current seed data, but the algorithm must not double-assign the same prior exercise to two slots; the "remove from pool once matched" step already prevents this if slots are processed in a stable, deterministic order).
- **Anchor slots are explicitly out of scope** — `last_valid_selections` only touches `kind in ("giant", "knee")` adaptive slots (fallback.py:71), unchanged by this fix.

## Dependencies
None — this is a standalone fix to one function. Does not depend on and is not depended on by any other in-flight spec.

## Verification
- New/updated test: seed a day with a prior COMPLETED session under the OLD exercise order, then change `TierExercise.exercise_order` (simulating a reorder, mirroring what `d4_reorder_knee_raise.py` does), call `last_valid_selections`, and assert the returned `Selections.ordering`/`slots` reflect the CURRENT (new) order with each movement correctly attached to its own slot — not the stale positional pairing.
- Regression test: an unchanged-order day still produces the same `Selections` as before the fix (byte-identical `ordering`/`slots` content, different code path).
- Meso-rotation edge case test: a slot whose prior exercise was a meso-2 swap movement still correctly resolves to that same movement under its current (possibly reordered) slot.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`.
- Manual: with the D4 reorder + Meadows Row load bump already live, artificially force a deviation signal for D4 (or use the existing test harness's way of forcing `should_invoke_llm=True`) and confirm `/generate` still shows the correct order and load even through the fallback path.
