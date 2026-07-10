# Spec 03: Fix the note-apply LOAD override for Hip Thrust (reframed from "verify" — found a real gap)

## Objective
The Day-2 note "ready to go up 5lbs on Day 5" (Hip Thrust) should flow through the existing note-apply LOAD-override mechanism. Investigation found the override mechanism is already correctly **day-scoped** (a `SlotMovementOverride` is keyed on `tier_exercise_id`, and D2/D5/D6 Hip Thrust are three distinct `TierExercise` rows, so a D5-only override cannot leak to D2/D6) — but it currently has **zero effect on Hip Thrust specifically**, because the assembler's HT branch resolves plates from `MovementState.ht_plates` and ignores the overridden scalar `load` entirely once `ht_plates` is set (true for every HT movement post go-live). This is not an AI-reasoning gap (§H) — it's a plain consumption-side bug. Fix it so a LOAD override actually bumps HT's plates.

## File targets
- Modify: `ironlog/generation/assembler.py` — `_build_exercise` (function containing lines 234-260): the override application at line 234 (`load, rep_low, rep_high = _apply_slot_override(...)`) and the HT block at lines 239-255 (`if _is_ht_movement(movement) ...: cur_plates, cur_config = _resolve_ht_current_setup(state, load)` at line 240, followed by `ht_next_setup(cur_plates, cur_config, band_inventory)` at line 255).

## Changes
1. In `_build_exercise`, after line 240 (`cur_plates, cur_config = _resolve_ht_current_setup(state, load)`) and before line 255 (`ht_next_setup(...)`), apply an active LOAD override (if any) to `cur_plates` — mirroring exactly how `_apply_slot_override` already adjusts the scalar `load` at line 234, but targeting `cur_plates` instead:
   ```python
   cur_plates = _apply_ht_load_override(db, tier_exercise_id, cur_plates)
   ```
   Add the helper (near `_apply_slot_override`, ~line 137-176, following its own structure/imports):
   ```python
   def _apply_ht_load_override(db: DBSession, tier_exercise_id: Optional[int],
                                plates: float) -> float:
       """Apply an active LOAD SlotMovementOverride to HT's resolved plates
       (Option-C: never writes MovementState/ht_plates — adjusts only the
       in-memory value fed into ht_next_setup, same pattern as
       _apply_slot_override's scalar-load adjustment)."""
       if tier_exercise_id is None:
           return plates
       from ..models.enums import OverrideType
       from ..models.program import SlotMovementOverride
       ov = db.exec(select(SlotMovementOverride).where(
           SlotMovementOverride.tier_exercise_id == tier_exercise_id,
           SlotMovementOverride.override_type == OverrideType.LOAD,
           SlotMovementOverride.active == True)).first()  # noqa: E712
       if ov is None:
           return plates
       if ov.load_absolute is not None:
           return ov.load_absolute
       if ov.load_delta is not None:
           return plates + ov.load_delta
       return plates
   ```
   Reuse the exact same `SlotMovementOverride`/`OverrideType` query pattern `_apply_slot_override` already uses (lines 150-163) — do not diverge on the filter logic (`override_type == OverrideType.LOAD`, `active == True`).
2. **Do not touch** `notes/apply.py` or the `/notes/{id}/apply` endpoint — the override *creation* path already correctly stores `load_delta`/`load_absolute` against a `tier_exercise_id` regardless of movement type; the bug is entirely on the *consumption* side in the assembler.
3. **Do not touch** `_resolve_ht_current_setup` itself — leave its `load` parameter and fallback behavior as-is (it's still correct for the "no ht_plates set yet" cold-start case); apply the override strictly after it resolves the base plates.

## Edge cases
- **Do not double-apply**: `_apply_slot_override` (line 234) already mutates the scalar `load` variable for non-HT movements. For an HT movement, that mutated `load` is currently discarded by `_resolve_ht_current_setup` (since `state.ht_plates is not None` takes precedence) — confirm your fix does NOT ALSO apply the override again via the scalar `load` path for HT movements (i.e., a LOAD override on an HT slot should bump plates exactly once, via `_apply_ht_load_override`, not also leak through the now-unused scalar `load`).
- **`load_absolute` vs `load_delta`**: mirror `_apply_slot_override`'s existing precedence (absolute wins if both are somehow set — though the API/apply layer should already enforce "exactly one of delta/absolute", per the original note-apply-redesign spec).
- **No active override**: `_apply_ht_load_override` returns `plates` unchanged — confirm zero behavior change for every HT session without an active LOAD override (regression-critical: D2/D5/D6 all currently generate correctly without this fix; this change must not perturb them).
- **Interaction with `ht_next_setup`'s own advancement**: the override-adjusted `cur_plates` becomes the *current* setup fed into `ht_next_setup`, which still computes the *next* setup as the staged `prospective_ht` (per K2's prescribe-current/advance-at-commit design, already live) — confirm the override affects what's PRESCRIBED this session (current, overridden) without disturbing the *next*-setup staging logic already in place downstream (lines ~255-262, unchanged).
- **Band config**: this spec only touches `cur_plates` (a LOAD override affects weight, not band selection) — `cur_config` is untouched, matching the note's actual request ("+5lbs", not a band change).

## Dependencies
None on Spec 01 or 02 (different file entirely: `assembler.py`, not touched by either). Independent — may build/merge in parallel.

## Verification
- `ssh myflix "cd ~/projects/IronLog-V2 && .venv/bin/pytest -q"` — full suite green.
- New test (extend `tests/test_ht_composite_wiring.py` or `tests/test_ht_generate_banded.py`, whichever already builds a real HT generation scenario — locate via `grep -rl "ht_next_setup\|_resolve_ht_current_setup" tests/`): create an active `SlotMovementOverride(override_type=LOAD, load_delta=5.0)` on D5 Hip Thrust's `tier_exercise_id`; generate D5; assert the prescribed `target_plates == 210.0` (205 seeded + 5 override), NOT 205. Then generate D2 and D6 in the same test and assert THEIR Hip Thrust plates are unaffected (180-track/155-track, whatever the current live baselines are) — proves the day-scoping (already correct) survives this fix.
- Regression: re-run the existing `tests/test_ht_generate_banded.py::test_banded_ht_generates_valid_all_days` (or equivalent) with NO override present and confirm D2/D5/D6 plates are unchanged from before this fix (180/205→whatever K2 currently prescribes/155 — use whatever the live/tested values are at merge time, not hardcoded from this spec).
