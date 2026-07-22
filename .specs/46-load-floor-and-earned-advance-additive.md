# Spec 46: Load floor and earned-advance step must stack additively, not max()

## Objective
`run_analysis.py`'s `pending_load_delta` computation silently discards a genuinely earned progression step whenever the athlete self-selected a heavier-than-prescribed load and the resulting load-floor correction exceeds the tiny per-session increment — the two mechanisms should combine additively, not via `max()`.

## Root cause (confirmed via live production data, not guessed)
`ironlog/persistence/run_analysis.py` line 493:
```python
d.pending_load_delta = max(adv.earned_load_step or 0.0, floor_delta)
```
`floor_delta` (`performed_floor_delta`, `ironlog/engine/advance.py`) exists so the next prescription never regresses below what the athlete actually lifted this session (a safety-net minimum). `earned_load_step` is the genuine reward for a clean advance — a real, athlete-earned increase (only ever set when `adv.advanced` is `True`; every rule that produces it — `_rpe8`, `_rule_driven`, `_single_session` in `ironlog/engine/advance.py` — leaves it `None` on any non-advancing branch, so `adv.earned_load_step or 0.0` is already correctly zero whenever no advance was earned).

`max()` picks whichever of these two is larger and discards the other entirely. This is silently correct only when the two never coexist meaningfully — but whenever an athlete self-selects meaningfully heavier than prescribed (not a rare edge case; confirmed twice in one real session — ATG Split Squat and Cable Tibialis Raise, 2026-07-21), `floor_delta` (5-10lbs, catching up to match what was actually lifted) exceeds the tiny per-session `increment_ladder` step (2.5lbs for both movements), and the athlete's actual earned clean-advance credit vanishes — the system merely catches up to what they already proved, banking zero forward progress for the clean session itself.

**Confirmed concrete case** (ATG Split Squat, movement_id=22, `increment_ladder=[2.5]`): prescribed 25lbs, athlete actually performed 30lbs cleanly (all reps hit, `ON_TARGET`). `floor_delta = performed_floor_delta(25, [30]*6) = 5.0`. `adv.earned_load_step = 2.5` (a genuine clean advance was earned). `max(2.5, 5.0) = 5.0` → next prescription = 30lbs — merely catching up to what was already proven, with the earned 2.5lb credit silently discarded. Correct next prescription: 32.5lbs (30 proven + 2.5 earned on top).

## The fix

In `ironlog/persistence/run_analysis.py`, change line 493 from `max()` to additive:
```python
d.pending_load_delta = floor_delta + (adv.earned_load_step or 0.0)
```
No other change needed at this call site — `floor_delta` is already computed above it (lines 480-487), and `adv.earned_load_step` is already correctly `None`/zero on any non-advancing branch (confirmed: every `AdvanceResult` in `ironlog/engine/advance.py` that returns `advanced=False` either omits `earned_load_step` entirely, defaulting to `None`, or the dataclass default applies — no rule sets a non-None `earned_load_step` alongside `advanced=False`). This means the fix correctly reduces to:
- `floor_delta` alone when the session wasn't clean/advancing but the athlete still lifted heavier than prescribed (no reward, but still never regresses below proven capacity — unchanged from today).
- `earned_load_step` alone when the athlete followed the prescription exactly and had a clean advance (`floor_delta == 0` in this case, since performed == prescribed — unchanged from today, e.g. this session's own Pendlay Row fix earlier: prescribed 175, performed 175, `floor_delta=0`, `earned=5`, sum=5, matches the `max()` result too since one operand is zero).
- **Both, stacked**, only in the case this spec fixes: a clean advance at a self-selected heavier-than-prescribed load.

## Edge cases
- `floor_delta` is already guaranteed non-negative (`performed_floor_delta`'s own docstring: "Never negative — a lighter-than-prescribed performance must not lower the next prescription") — the additive sum can never decrease `pending_load_delta` relative to today's `max()` result, only increase it in the one case this spec targets. No regression risk for any currently-passing scenario.
- Movements excluded from the floor computation entirely (non-`current_load`-field progression modes, `HIP_THRUST` per the existing `if` guard above line 493) are unaffected — `floor_delta` stays `0.0` for them exactly as today, so the additive change is a no-op for those movements.
- A session that is NOT clean/advancing (`adv.earned_load_step is None`) but the athlete self-selected LIGHTER than prescribed: `performed_floor_delta` already returns `0.0` for any performed load at or below `current_load` (its own docstring: "no performed_loads exceed it" → 0.0) — no behavior change here either.

## Dependencies
None.

## Verification
- **New test** in `tests/test_run_analysis_progression.py`: construct a movement with a small `increment_ladder` (e.g. `[2.5]`), seed `MovementState.current_load` below what the session's `SetLog.actual_load` rows record (e.g. prescribed/current 25.0, all working sets logged at `actual_load=30.0`, clean/`ON_TARGET`, hitting `target_reps_high`), run `run_analysis`, and assert `MovementDelta.pending_load_delta == 7.5` (5.0 floor + 2.5 earned) — NOT `5.0` (today's buggy `max()` result). This is the direct regression test for the bug this spec fixes.
- **New test**: same setup but the session is NOT clean (e.g. one set misses `target_reps_high`) — assert `pending_load_delta == 5.0` (floor only, no earned credit, matching today's behavior — confirms the fix doesn't reward a non-advancing session).
- Existing tests in `test_run_analysis_progression.py` (`test_t1_clean_session_advances_and_sets_active_rule`, `test_accessory_needs_two_clean_sessions_to_advance`, `test_stall_signal_fires_and_clears_on_advance`, all asserting `pending_load_delta == 2.5`) must all still pass unchanged — none of their fixtures involve a performed-load-exceeds-prescription scenario, so `floor_delta` stays `0.0` in each and `0.0 + 2.5 == 2.5` matches their existing assertions exactly.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 653 passing — expect 655 with the 2 new tests, zero regressions).
