# Spec 37: Wire weak-point signal computation into run_analysis.py

## Objective
Compute and store one `MovementWeaknessSignal` row per analyzed movement after each session, combining the existing `detect_stall` with the new `compute_growth_rate`/`compute_lagging` (specs 35, 36 — both must be merged first), per the design doc §Components 3.

## File targets
- Modify: `ironlog/persistence/run_analysis.py` — add the weak-point signal computation inside/after the existing per-movement loop.
- New tests: `tests/test_weak_point_signal_wiring.py` (or extend an existing `run_analysis.py` integration test file if one is a better fit — check `tests/test_run_analysis.py`/`tests/test_run_analysis_progression.py` first and follow whichever this repo's convention favors for a new run_analysis.py behavior).

## Important grounding — `detect_stall` is NOT currently called anywhere in `run_analysis.py`
This file's own module docstring (lines 1-13) explicitly states `detect_stall` is a v0.6 GENERATION-time consumer (called from `ironlog/generation/context.py`'s `build_weak_point_hints`), not something `run_analysis.py` calls today. This spec is the first time `detect_stall` gets invoked during the ANALYZE step — you are wiring a new call, not reusing an existing one. Read the module docstring after your change and update it if it's now inaccurate (it currently says "detect_stall is NOT called here" — that sentence becomes false once this spec lands, update or remove it).

## The fix

The existing per-movement loop (search for `for mid in movement_ids:` — there's an earlier loop around line 288 that builds `movements_inputs`/`state_by_mv`/`movement_by_mv`, and a LATER, separate loop around line 347 that computes `recent_progress`/RPE-creep/strength-bounce signals from `E1rmHistory`). The `recent_progress` list built in the second loop (`progress_rows[-STALL_WINDOW:]`, sorted oldest-first) is exactly the `progress_anchor_e1rms` shape `detect_stall` expects.

Within (or right after) that second loop, for each `mid`:
1. Call `detect_stall(progress_anchor_e1rms=[r.e1rm for r in recent_progress], consecutive_failed=state_by_mv[mid].consecutive_failed_progressions, objective=resolve_objective(movement_by_mv[mid].objective_override, phase_default))` — reuse `state_by_mv`/`movement_by_mv`/`phase_default`, already resolved in the first loop; do not re-query the DB for data already fetched.
2. Call `compute_growth_rate([r.e1rm for r in recent_progress])` → `this_rate`.
3. Collect `this_rate` for every movement into a dict `{mid: rate}` — you need ALL movements' rates before you can compute any single movement's `lagging` value (the comparison population is "the athlete's OTHER movements"), so this requires either two passes over `movement_ids`, or collecting rates in the same loop and doing the `lagging` computation in a second, short pass after the loop completes. Prefer the two-pass structure for clarity — do not try to compute `lagging` inline in the same iteration that's still building the rate dict for movements not yet visited.
4. After the rate dict is complete, for each `mid`: call `compute_lagging(this_movement_rate=rates[mid], other_movement_rates=[r for m, r in rates.items() if m != mid])`.
5. Write one `MovementWeaknessSignal` row per movement: `stalled=stall_signal.stalled, growth_rate=this_rate, lagging=lagging_result, is_weak=(stall_signal.stalled or lagging_result), movement_id=mid, session_id=session_id`.
6. `db.add()` each row (do not commit separately from the rest of this function's existing commit — ride along with whatever commit already happens at the end of `run_analysis`, confirm where that is and add these rows before it).

You'll need `from ..engine.stall import detect_stall, compute_growth_rate, compute_lagging` added to the existing `from ..engine.stall import (...)` import (currently imports `STALL_WINDOW, build_stall_signal` — check if `build_stall_signal` still needs importing or if it's unused after your change; do not remove an import something else in this file still uses) and `from ..models.library import MovementWeaknessSignal` added to the existing model imports.

## Edge cases
- **A movement with fewer than 2 PROGRESS anchors** — `compute_growth_rate` returns `None` for it; it still gets a `MovementWeaknessSignal` row (with `growth_rate=None`, `lagging=False`), and `stalled` is computed independently (via `detect_stall`, which has its own `STALL_MIN_SESSIONS` gate and returns all-`False` on insufficient data or a non-PROGRESS objective).
- **Fewer than 3 movements total with a computable rate** — every movement's `lagging` comes out `False` (per `compute_lagging`'s own minimum-population guard from spec 35) — `is_weak` still correctly reflects `stalled` alone in this case, not silently broken.
- **A non-PROGRESS-objective movement** — `detect_stall` already returns `StallSignal(False, False, False)` for these (existing behavior, don't change it) — such a movement can still show `lagging=True` if its growth rate genuinely lags (growth rate isn't objective-gated the way stall is, per the design doc — MAINTAIN/MEASURE movements can still be meaningfully compared for relative growth). Do not add objective-gating to `compute_lagging` that isn't in its spec 35 signature.
- Do not modify `detect_stall`, `compute_growth_rate`, or `compute_lagging` themselves (specs 35/36, already merged/reviewed) — only call them.
- Do not touch the six STAB→REBUILD booleans, the goal-gate wiring, or any other existing `run_analysis.py` computation — purely additive.

## Dependencies
Depends on spec 35 (`compute_growth_rate`/`compute_lagging`) AND spec 36 (`MovementWeaknessSignal`) both merged first.

## Verification
- New integration test(s): seed multiple movements with known `E1rmHistory` (a clearly-lagging movement, a clearly-non-lagging one, and a stalled-but-not-lagging one) across enough sessions to produce a real comparison population, run `run_analysis`, assert the resulting `MovementWeaknessSignal` rows match expectations (correct `is_weak` per movement, traced through real threshold math, not tautological values).
- A regression test proving `run_analysis` still works correctly with FEWER than 3 movements having computable rates (the `lagging=False`-for-everyone fallback case) — do not let this silently break the whole analysis run.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 606 passing, or higher if specs 35/36 already merged in this batch — check `main`'s actual count before dispatching).

## Human gate note
This adds new analysis logic (a median-comparison computation across movements) but does not modify any existing decision/gate logic (unlike the earlier goal-driven-phase-gate batch's spec 32) — it's purely additive analytics. Standard risk-routed review applies (non-trivial new logic → review), but this is NOT flagged for unconditional/mandatory review the way a gate-logic change would be. Use judgment; when in doubt, review.
