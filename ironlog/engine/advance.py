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
    session_performed: bool = False       # RULE_DRIVEN: RPE-exempt, just "did it happen" (Task 3)
    last_set_hit_target: bool = False     # SINGLE_SESSION: last-set-only gate (Task 3)


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


def _at_cap(state, movement) -> bool:
    return (state.current_load is not None and movement.cap is not None
            and state.current_load >= movement.cap)


def _rule_driven(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.RULE_DRIVEN.value
    if _at_cap(state, movement):
        # ceiling reached: hand off to the rep-ladder rule (rep_target seeds
        # to rep_ladder[0] via _rep_ladder's own None-current-target branch);
        # the returned active_rule is REP_LADDER — the caller persists the switch.
        return _rep_ladder(state, perf, movement, window)
    if not perf.session_performed:                        # RPE-exempt: ignore hit_target/max_rpe
        return AdvanceResult(False, rule, 0)
    streak = state.consecutive_advance_count + 1
    if streak >= window:
        ladder_len = len(movement.increment_ladder or [])
        new_tier = min(state.current_increment_tier + 1, ladder_len - 1)
        return AdvanceResult(True, rule, 0, new_tier=new_tier)
    return AdvanceResult(False, rule, streak)


def _single_session(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.SINGLE_SESSION.value
    if perf.last_set_hit_target and perf.max_rpe <= 8.0:   # window is always 1 for this rule
        ladder_len = len(movement.increment_ladder or [])
        new_tier = min(state.current_increment_tier + 1, ladder_len - 1)
        return AdvanceResult(True, rule, 0, new_tier=new_tier)
    return AdvanceResult(False, rule, 0)


def _rep_ladder(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.REP_LADDER.value
    ladder = movement.rep_ladder or []
    current = getattr(state, "current_rep_target", None)
    if current is None:                                    # freshly transitioned onto the ladder
        return AdvanceResult(False, rule, 0, new_rep_target=ladder[0] if ladder else None)
    if not _clean(perf) or current not in ladder:
        return AdvanceResult(False, rule, 0, new_rep_target=current)
    streak = state.consecutive_advance_count + 1
    if streak < window:
        return AdvanceResult(False, rule, streak, new_rep_target=current)
    idx = ladder.index(current)
    if idx >= len(ladder) - 1:                              # terminal rung -> maintenance
        return AdvanceResult(False, rule, 0, new_rep_target=current)
    return AdvanceResult(True, rule, 0, new_rep_target=ladder[idx + 1])


def _fixed_load(state, perf, movement, window) -> AdvanceResult:
    return AdvanceResult(False, ProgressionRule.FIXED_LOAD.value, state.consecutive_advance_count)


_DISPATCH = {
    ProgressionRule.RPE_8_STANDARD: _rpe8,
    ProgressionRule.RULE_DRIVEN: _rule_driven,
    ProgressionRule.SINGLE_SESSION: _single_session,
    ProgressionRule.REP_LADDER: _rep_ladder,
    ProgressionRule.FIXED_LOAD: _fixed_load,
}


def advance(rule, state, perf, movement, confirmation_window) -> AdvanceResult:
    fn = _DISPATCH.get(rule)
    if fn is None:
        # fallback invariant: unknown/unhandled rule -> no change (spec §9)
        return AdvanceResult(False, getattr(rule, "value", str(rule)), state.consecutive_advance_count)
    return fn(state, perf, movement, confirmation_window)
