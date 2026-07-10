# Spec 02: Load ratchet — never prescribe below a logged actual (L)

## Objective
When an athlete performs a scalar-load (`current_load`-mode) movement heavier than the seeded/current baseline in a clean, on-target session, the next prescription must never regress below what was actually lifted — today it silently reverts to the pre-session baseline. Concrete case: Belt Squat seeded 260, athlete logged 265×12 at RPE 8 (off-script heavier by reorganizing plates); the engine currently re-prescribes 260.

## File targets
- Modify: `ironlog/engine/advance.py` — add a pure helper, e.g. `def performed_floor_delta(current_load: Optional[float], performed_loads: List[float]) -> float` (place near `_earned_step`, ~line 44 area; keep it a standalone pure function, not folded into `_earned_step` itself, since it applies regardless of which `ProgressionRule` fired or whether `advance()` returned `advanced=True`).
- Modify: `ironlog/persistence/run_analysis.py` — in the per-movement loop (~lines 326-374, where `d.pending_load_delta = adv.earned_load_step` is set at line 373), fold in the performed-floor check. Needs the session's own `set_logs` for this `mid` (already in scope — `set_logs` is passed into `_build_session_perf` at line 328) and `movement.progression_mode` (via `movement_by_mv[mid]`, already in scope) to gate this to `current_load`-mode movements only.

## Changes
1. `advance.py`: add
   ```python
   def performed_floor_delta(current_load: Optional[float], performed_loads: List[float]) -> float:
       """The minimum load bump required so the next prescription is never below
       the heaviest weight actually logged this session. Returns 0.0 if current_load
       is None (needs-calibration — nothing to floor against) or no performed_loads
       exceed it. Never negative."""
       if current_load is None or not performed_loads:
           return 0.0
       heaviest = max(performed_loads)
       return max(heaviest - current_load, 0.0)
   ```
2. `run_analysis.py`, inside the per-movement `try` block (after `perf = _build_session_perf(...)`, before or alongside the existing `adv = advance(...)` call at line 330):
   - Only for `movement.progression_mode` where `load_field_for_mode(movement.progression_mode) == "current_load"` (LADDER or COMPOSITE) — but see Edge Cases below re: excluding HT/COMPOSITE specifically.
   - Collect `performed_loads = [sl.actual_load for sl in set_logs if sl.movement_id == mid and not sl.is_warmup and sl.actual_load is not None]`.
   - Compute `floor_delta = performed_floor_delta(state.current_load, performed_loads)`.
   - At the existing assignment site (line 373 area): `d.pending_load_delta = max(adv.earned_load_step or 0.0, floor_delta) if (adv.earned_load_step is not None or floor_delta > 0) else None` — i.e., the delta staged for commit is whichever is larger: the rule-driven clean-advance step, or the floor needed to not regress below what was performed. Do NOT let a `floor_delta == 0.0` overwrite an already-`None` `d.pending_load_delta` with `0.0` — preserve the existing "None means no-op" contract `apply.py:104` (`if d.pending_load_delta is not None`) relies on.

## Edge cases
- **HT/COMPOSITE movements are OUT OF SCOPE for this spec.** Hip Thrust's load is `ht_plates`/`ht_band_config`, not `current_load` — `load_field_for_mode(COMPOSITE)` returns `"current_load"` but HT's actual scalar `current_load` field is unused (see Spec 03, a *related but separate* gap in the HT override path). Explicitly exclude `movement.lift_category == LiftCategory.HIP_THRUST` (or `movement.progression_mode == ProgressionMode.COMPOSITE`, matching `_is_ht_movement`'s own check in `assembler.py:177`) from the floor computation in this spec — do not attempt to floor `ht_plates` here.
- A rep-ladder movement (Belt Squat's actual `progression_rule` is `REP_LADDER`, not `RPE_8_STANDARD` — confirm via `SELECT progression_rule FROM movement WHERE name='Belt Squat [GHR + FT]'` on the live/test DB) may return `adv.earned_load_step is None` (rep-ladder advances `rep_target`, not `current_load`) while still needing the floor from a heavier logged weight — this is exactly why the floor check must be independent of `adv.earned_load_step`, not gated on it.
- Multiple working sets logged at different loads in one session (e.g. a top set heavier than backoff sets) — `max(performed_loads)` correctly picks the heaviest, which is the right floor (never regress below the heaviest thing actually lifted, even if a later set in the same session was lighter/backoff).
- A logged `actual_load` LOWER than `current_load` (the athlete went lighter than prescribed) must not lower the prescription — `performed_floor_delta` already returns `0.0` (never negative) in this case; confirm no accidental subtraction elsewhere.
- Day-scoping: `state` here is already the day-scoped `MovementState` row (via `state_by_mv[mid]`, resolved upstream per the existing `(movement_id, day_id)` composite-key day-scoping) — no additional day-scoping needed in this spec, just don't break it.

## Dependencies
None on Spec 01 or 03 (touches different files entirely). Independent — may build/merge in parallel with 01 and 03.

## Verification
- `ssh myflix "cd ~/projects/IronLog-V2 && .venv/bin/pytest -q"` — full suite green.
- New unit test for `performed_floor_delta` (pure, e.g. in `tests/test_advance_load_bridge.py`, the file K2 already added): `current_load=260, performed_loads=[265.0]` → `5.0`; `current_load=260, performed_loads=[250.0]` → `0.0`; `current_load=None, performed_loads=[265.0]` → `0.0`; `current_load=260, performed_loads=[]` → `0.0`.
- End-to-end test mirroring the existing Bench 165→170→175 ratchet test (`tests/test_advance_load_bridge.py`, from K2): seed Belt Squat at 260 (matches the live baseline), log a session with a working set at 265×12 (RPE 8, ON_TARGET or the tap that yields `max_rpe<=8`), run `run_analysis`, assert `pending_load_delta == 5.0` even though Belt Squat's rule is `REP_LADDER` (not a clean `RPE_8_STANDARD` advance) — this is the case that would fail before this fix. Regenerate and confirm the next Belt Squat prescription is `265`, not `260`.
- Confirm the Option-C guardrail test (`tests/test_write_boundary.py` or `tests/test_ht_write_boundary.py`) stays green — this spec must not write `current_load` directly, only stage `pending_load_delta` (same mechanism as K2).
