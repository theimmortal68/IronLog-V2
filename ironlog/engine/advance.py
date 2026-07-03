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
    unassisted_set1_reps: Optional[int] = None  # PULL_UP_ROLLING_MAX: set-1 unassisted reps (Task 4)


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


def _ladder_step(state, perf, window, rule, ladder, current, result_field) -> AdvanceResult:
    """Shared streak/reset stepping logic for the assist/position ladders.

    Mirrors `_rep_ladder`'s shape: seed the ladder when `current` is None,
    hold (echoing `current` back via `result_field`) on a dirty session or an
    off-ladder value, advance to the next rung once the streak clears
    `window`, and hold at the terminal rung once reached.
    """
    if current is None:                                    # freshly transitioned onto the ladder
        return AdvanceResult(False, rule, 0, **{result_field: ladder[0] if ladder else None})
    if not _clean(perf) or current not in ladder:
        return AdvanceResult(False, rule, 0, **{result_field: current})
    streak = state.consecutive_advance_count + 1
    if streak < window:
        return AdvanceResult(False, rule, streak, **{result_field: current})
    idx = ladder.index(current)
    if idx >= len(ladder) - 1:                              # terminal rung -> maintenance
        return AdvanceResult(False, rule, 0, **{result_field: current})
    return AdvanceResult(True, rule, 0, **{result_field: ladder[idx + 1]})


def _incline_reduction(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.INCLINE_REDUCTION.value
    ladder = movement.assist_ladder or []
    return _ladder_step(state, perf, window, rule, ladder, state.assist_level, "new_assist_level")


def _assistance_reduction(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.ASSISTANCE_REDUCTION.value
    ladder = movement.assist_ladder or []
    result = _ladder_step(state, perf, window, rule, ladder, state.assist_level, "new_assist_level")
    if result.advanced and ladder and result.new_assist_level == ladder[-1]:
        # BW/unassisted terminal reached -> hand off to loaded RPE-8 progression (spec §1.5)
        result.active_rule = ProgressionRule.RPE_8_STANDARD.value
    return result


def _body_position(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.BODY_POSITION.value
    ladder = movement.position_ladder or []
    return _ladder_step(state, perf, window, rule, ladder, state.current_body_position, "new_body_position")


def _pull_up_rolling_max(state, perf, movement, window) -> AdvanceResult:
    # tracking-only this chunk: no load/assist change, no cross-day action.
    # The persistence layer (Task 6) calls `roll_unassisted_max` to update
    # `MovementState.unassisted_max_rolling` from `perf.unassisted_set1_reps`.
    return AdvanceResult(False, ProgressionRule.PULL_UP_ROLLING_MAX.value, state.consecutive_advance_count)


def roll_unassisted_max(prev: Optional[int], set1_reps: Optional[int]) -> Optional[int]:
    """Pure rolling-max update for pull-up tracking (simplest correct form for beta)."""
    return max(prev or 0, set1_reps or 0)


_DISPATCH = {
    ProgressionRule.RPE_8_STANDARD: _rpe8,
    ProgressionRule.RULE_DRIVEN: _rule_driven,
    ProgressionRule.SINGLE_SESSION: _single_session,
    ProgressionRule.REP_LADDER: _rep_ladder,
    ProgressionRule.FIXED_LOAD: _fixed_load,
    ProgressionRule.INCLINE_REDUCTION: _incline_reduction,
    ProgressionRule.ASSISTANCE_REDUCTION: _assistance_reduction,
    ProgressionRule.BODY_POSITION: _body_position,
    ProgressionRule.PULL_UP_ROLLING_MAX: _pull_up_rolling_max,
}


def advance(rule, state, perf, movement, confirmation_window) -> AdvanceResult:
    fn = _DISPATCH.get(rule)
    if fn is None:
        # fallback invariant: unknown/unhandled rule -> no change (spec §9)
        return AdvanceResult(False, getattr(rule, "value", str(rule)), state.consecutive_advance_count)
    return fn(state, perf, movement, confirmation_window)
