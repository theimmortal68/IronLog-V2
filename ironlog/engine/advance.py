"""
advance.py — pure rule-dispatch core for the progression engine.

`advance()` is the stable entry point later tasks (3-6) extend by registering
more rules in `_DISPATCH`. It is pure: no DB, no HTTP — plain dataclass
inputs (`SessionPerf`) plus the `MovementState`/`Movement` objects in, an
`AdvanceResult` out. `AdvanceResult` carries only earned deltas — it NEVER
carries `current_load` (Option C: the engine earns the load *step*, the caller
stages it via `MovementState.pending_load_delta` and `commit_session` derives
`current_load` at generation time).

Scalar-load semantics (K2, docs/04 §"current_increment_tier"): a clean advance
RAISES the working load by `increment_ladder[current_increment_tier]` (returned
as `earned_load_step`). It does NOT touch `current_increment_tier` — that is the
step-SIZE index, which steps DOWN on stall (`step_down_tier`, in analysis.py) and
up only on sustained breakthrough. Bumping it on a clean advance shrinks the step
instead of adding load, which was backwards.
"""
from dataclasses import dataclass
from typing import List, Optional

from ..models.enums import LiftCategory, ProgressionMode, ProgressionRule


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
    active_rule: Optional[str]
    consecutive_advance_count: int
    new_tier: Optional[int] = None
    new_assist_level: Optional[float] = None
    new_rep_target: Optional[int] = None
    new_body_position: Optional[str] = None
    new_duration_seconds: Optional[int] = None
    new_rope: Optional[str] = None
    earned_load_step: Optional[float] = None   # K2: scalar load step earned on a clean advance


def _clean(perf: SessionPerf) -> bool:
    return perf.hit_target and perf.max_rpe <= 8.0 and perf.all_sides_cleared


def _earned_step(state, movement) -> Optional[float]:
    """The load step a clean advance earns: `increment_ladder[current_increment_tier]`.

    None when the movement has no increment_ladder (defensive — scalar LADDER
    movements always carry one). The tier index is clamped into range so a
    transient out-of-bounds tier can never IndexError the whole analyze step.
    """
    ladder = movement.increment_ladder or []
    if not ladder:
        return None
    idx = min(max(state.current_increment_tier, 0), len(ladder) - 1)
    return ladder[idx]


def performed_floor_delta(current_load: Optional[float], performed_loads: List[float]) -> float:
    """The minimum load bump required so the next prescription is never below
    the heaviest weight actually logged this session (L: the load ratchet).

    Returns 0.0 if current_load is None (needs-calibration -- nothing to floor
    against) or no performed_loads exceed it. Never negative -- a lighter-than-
    prescribed performance must not lower the next prescription.
    """
    if current_load is None or not performed_loads:
        return 0.0
    heaviest = max(performed_loads)
    return max(heaviest - current_load, 0.0)


def performed_assist_floor(current: Optional[float], ladder: List[float],
                            clean_performed_values: List[float]) -> Optional[float]:
    """The most advanced (highest ladder-index) value demonstrated by a clean
    (rep-target-hit) set this session, if more advanced than `current`.
    Returns `current` unchanged if no clean value is more advanced, or if
    `current`/ladder is unusable (current is None, or off-ladder). Never
    regresses backward on the ladder. Direction-agnostic: 'more advanced'
    means a higher ladder index, regardless of whether the ladder ascends or
    descends numerically.
    """
    if current is None or not ladder or current not in ladder:
        return current
    current_idx = ladder.index(current)
    candidate_indices = [ladder.index(v) for v in clean_performed_values if v in ladder]
    if not candidate_indices:
        return current
    best_idx = max(candidate_indices)
    return ladder[best_idx] if best_idx > current_idx else current


def _rpe8(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.RPE_8_STANDARD.value
    if not _clean(perf):
        return AdvanceResult(False, rule, 0)                      # any miss resets the streak
    streak = state.consecutive_advance_count + 1
    if streak >= window:
        # Clean advance RAISES the load by the current step; the tier is untouched.
        return AdvanceResult(True, rule, 0, earned_load_step=_earned_step(state, movement))
    return AdvanceResult(False, rule, streak)


def _at_cap(state, movement) -> bool:
    return (state.current_load is not None and movement.cap is not None
            and state.current_load >= movement.cap)


def _is_ht_composite(movement) -> bool:
    """HT (band-composite) detection — mirrors validator._check_ht_safety /
    generation.assembler._is_ht_movement."""
    return (getattr(movement, "lift_category", None) == LiftCategory.HIP_THRUST
            or getattr(movement, "progression_mode", None) == ProgressionMode.COMPOSITE)


def _rule_driven(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.RULE_DRIVEN.value
    if _is_ht_composite(movement):
        # Band-composite HT: the assembler (ht_next_setup, Task 4) resolves the
        # next (plates, band_config) setup at generation time from ht_plates/
        # ht_band_config, not from current_load/tier — this rule always no-ops
        # for COMPOSITE/HIP_THRUST movements, at-cap or not, preserving the
        # streak rather than resetting or switching rules. One progression
        # path, not two.
        return AdvanceResult(False, rule, state.consecutive_advance_count)
    if _at_cap(state, movement):
        # ceiling reached: hand off to the rep-ladder rule (rep_target seeds
        # to rep_ladder[0] via _rep_ladder's own None-current-target branch);
        # the returned active_rule is REP_LADDER — the caller persists the switch.
        # This is a separate 2-session rule, so it gets the ORIGINAL received
        # window, not the pre-cap hardcoded window below.
        return _rep_ladder(state, perf, movement, window)
    if not perf.session_performed:                        # RPE-exempt: ignore hit_target/max_rpe
        return AdvanceResult(False, rule, 0)
    # Pre-cap tier-advance is every-session (spec §1.3), regardless of the
    # confirmation_window a non-T1 tier label would otherwise resolve to —
    # hardcode window=1 here, mirroring _single_session's own hardcoded gate.
    streak = state.consecutive_advance_count + 1
    if streak >= 1:
        # Clean advance RAISES the load by the current step; the tier is untouched.
        return AdvanceResult(True, rule, 0, earned_load_step=_earned_step(state, movement))
    return AdvanceResult(False, rule, streak)


def _single_session(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.SINGLE_SESSION.value
    if perf.last_set_hit_target and perf.max_rpe <= 8.0:   # window is always 1 for this rule
        # Clean advance RAISES the load by the current step; the tier is untouched.
        return AdvanceResult(True, rule, 0, earned_load_step=_earned_step(state, movement))
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


def _finisher_duration_then_rope(state, perf, movement, window) -> AdvanceResult:
    rule = ProgressionRule.FINISHER_DURATION_THEN_ROPE.value
    duration_ladder = state.duration_ladder or []
    current_duration = state.current_duration_seconds
    current_rope = state.current_rope
    duration_result = _ladder_step(
        state,
        perf,
        window,
        rule,
        duration_ladder,
        current_duration,
        "new_duration_seconds",
    )

    if not duration_ladder or current_duration not in duration_ladder or not _clean(perf):
        duration_result.new_rope = current_rope
        return duration_result

    streak = state.consecutive_advance_count + 1
    if streak < window:
        duration_result.new_rope = current_rope
        return duration_result

    duration_idx = duration_ladder.index(current_duration)
    if duration_idx < len(duration_ladder) - 1:
        duration_result.new_rope = current_rope
        return duration_result

    rope_ladder = movement.rope_ladder or []
    if current_rope not in rope_ladder:
        duration_result.new_rope = current_rope
        return duration_result

    rope_idx = rope_ladder.index(current_rope)
    if rope_idx >= len(rope_ladder) - 1:
        duration_result.new_rope = current_rope
        return duration_result

    return AdvanceResult(
        True,
        rule,
        0,
        new_duration_seconds=duration_ladder[0],
        new_rope=rope_ladder[rope_idx + 1],
    )


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
    ProgressionRule.FINISHER_DURATION_THEN_ROPE: _finisher_duration_then_rope,
    ProgressionRule.PULL_UP_ROLLING_MAX: _pull_up_rolling_max,
}


def advance(rule, state, perf, movement, confirmation_window) -> AdvanceResult:
    fn = _DISPATCH.get(rule)
    if fn is None:
        # fallback invariant: unknown/unhandled rule -> no change (spec §9).
        # active_rule is None here (never the stringified "None") — every
        # seeded movement today has progression_rule=None (live config is a
        # deferred follow-on), and the apply layer's `if d.active_rule is not
        # None` guard depends on this to correctly skip the write.
        return AdvanceResult(False, None, state.consecutive_advance_count)
    return fn(state, perf, movement, confirmation_window)
