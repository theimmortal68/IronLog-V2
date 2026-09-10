# Spec 05b: Finisher progression engine rule (decomposed from Spec 05)

## Objective
Implement the D6 jump-rope finisher's duration→rope-weight progression as a new `advance.py` dispatch rule: `FINISHER_DURATION_THEN_ROPE`. Steps `duration_ladder` via the existing `_ladder_step` primitive; on reaching the duration ladder's terminal rung, swaps to the next `rope_ladder` entry and resets duration to the ladder's first rung, rather than holding. Depends on Spec 05a's schema (must be merged first).

## Background
Read `05-finisher-emom.md` (the parent design doc) for full rationale. The precedent to follow structurally is `_assistance_reduction` (`ironlog/engine/advance.py:187-194`) — it steps one ladder and on reaching its terminal rung switches `active_rule` to a different rule. This spec's rule is similar but **resets** the first ladder (duration) to its start instead of abandoning it, while advancing the second ladder (rope) — a shape with no existing precedent in this file, so write it as a new, clearly-named function, not a strained reuse of an existing one.

## File targets
- Modify: `ironlog/engine/advance.py`:
  - New function `_finisher_duration_then_rope(state, perf, window)` — pure, no DB/HTTP access, same signature shape as the other rule functions in this file (e.g. `_assistance_reduction`, `_rep_ladder`).
  - Logic: read `state.duration_ladder` (from `MovementState`, seeded by 05a) and `state.current_duration_seconds`; use `_ladder_step`'s existing stepping logic (seed-if-None, hold-if-dirty/off-ladder, advance-if-streak-clears-window) to determine the *would-be* next duration rung. If the current rung is already the ladder's terminal rung AND the step condition is met (i.e. this session would have advanced past the end): instead of holding, look up `state.movement.rope_ladder` (static, on `Movement`) and `state.current_rope`; advance to the next rope rung (or hold if `current_rope` is already the terminal rope rung — this is the true terminal state, matching `05-finisher-emom.md`'s edge case), and reset `current_duration_seconds` to `duration_ladder[0]`.
  - Register `_finisher_duration_then_rope` in the `_DISPATCH: Dict[ProgressionRule, Callable]` mapping (`advance.py:215-225`) under `ProgressionRule.FINISHER_DURATION_THEN_ROPE` (added in 05a).
- New test file: `tests/test_finisher_progression.py` (or add to an existing `advance`-focused test file if one exists — check `tests/` for the naming convention used by other rule tests, e.g. whatever file tests `_assistance_reduction`, and mirror it).

## Edge cases
- **True terminal state**: `current_rope="one_lb"` (last rung) AND the duration ladder would otherwise advance past its own terminal (50s) — HOLD, no further change to either field. Do not index past either ladder's bounds.
- **Off-ladder / dirty state**: if `current_duration_seconds` isn't found in `duration_ladder` (shouldn't happen from seeded state, but mirror the existing `_ladder_step` dirty-state handling for consistency) — hold, do not crash.
- **This function must not touch `current_load` or any load-related field** — a finisher's `MovementState` never carries `current_load` semantics; verify no code path in this function reads/writes it.
- The function is exercised by `advance()`'s existing dispatch mechanism (`advance.py:228-237`) with a `Movement`/`MovementState`/`SessionPerf` — it does not need new plumbing into how `advance()` itself is called; only the `_DISPATCH` table entry is new wiring.

## Dependencies
**Depends on Spec 05a merging first** — this rule reads `MovementState.duration_ladder`/`current_duration_seconds`/`current_rope` and `Movement.rope_ladder`, all of which 05a creates. Branch this worktree from 05a's post-merge `main`.

## Verification
- New test: seed a `MovementState` at an early duration rung, drive `_finisher_duration_then_rope` through enough clean sessions (per whatever "clean streak" window the existing `_ladder_step` mechanism uses) to cross the duration ladder's terminal rung — assert the rope advances to the next rung and duration resets to the ladder's first value.
- New test: drive to the true terminal state (last rope + last duration) — assert it holds, no further change.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`.
- This spec still produces no `/generate` behavior change — 05c wires the rule into actual session generation and finisher display.
