"""
advance.py — pure rule-dispatch core for the progression engine.

`advance()` is the stable entry point later tasks (3-6) extend by registering
more rules in `_DISPATCH`. It is pure: no DB, no HTTP — plain dataclass
inputs (`SessionPerf`) plus the `MovementState`/`Movement` objects in, an
`AdvanceResult` out. `AdvanceResult` carries only earned deltas — it NEVER
carries `current_load` (Option C: the engine writes the tier index; the
caller derives `current_load` from the tier at generation time).
"""
from dataclasses import dataclass
from typing import Optional

from ..models.enums import ProgressionRule


@dataclass
class SessionPerf:
    hit_target: bool          # all working sets hit rep_high (both sides for unilateral)
    max_rpe: float            # highest RPE across working sets
    all_sides_cleared: bool   # unilateral AND-gate (True for bilateral)


@dataclass
class AdvanceResult:
    advanced: bool
    active_rule: str
    consecutive_advance_count: int
    new_tier: Optional[int] = None
    new_assist_level: Optional[float] = None
    new_rep_target: Optional[int] = None
    new_body_position: Optional[str] = None


def _clean(perf: SessionPerf) -> bool:
    return perf.hit_target and perf.max_rpe <= 8.0 and perf.all_sides_cleared


def _rpe8(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.RPE_8_STANDARD.value
    if not _clean(perf):
        return AdvanceResult(False, rule, 0)                      # any miss resets the streak
    streak = state.consecutive_advance_count + 1
    if streak >= window:
        ladder_len = len(movement.increment_ladder or [])
        new_tier = min(state.current_increment_tier + 1, ladder_len - 1)
        return AdvanceResult(True, rule, 0, new_tier=new_tier)
    return AdvanceResult(False, rule, streak)


_DISPATCH = {ProgressionRule.RPE_8_STANDARD: _rpe8}


def advance(rule, state, perf, movement, confirmation_window) -> AdvanceResult:
    fn = _DISPATCH.get(rule)
    if fn is None:
        # fallback invariant: unknown/unhandled rule -> no change (spec §9)
        return AdvanceResult(False, getattr(rule, "value", str(rule)), state.consecutive_advance_count)
    return fn(state, perf, movement, confirmation_window)
