"""
analysis.py — pure-logic post-session analysis (the ANALYZE step, docs/06 §0).

Computes an AnalysisResult of proposed state deltas from a just-logged session
plus current state. PURE: no DB, no network, no LLM. The writes happen in
persistence/apply.py (the single write point), mirroring the validator's
"compute violations, caller applies" contract.

One shared anchor — the best working set by estimate_e1rm — drives BOTH the
e1RM value and the outcome classification (CEILING/MISS/NEITHER). Measurements
(e1RM, phase-gate eval) are objective-independent; prescription machinery
(ceiling/failed counters, tier step-down) is PROGRESS-gated. The hook never
writes current_load (generation's job) or current_phase (report-only).

See docs/superpowers/specs/2026-06-25-analysis-hook-design.md for the full spec.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..models.enums import FeedbackTap, Objective, Phase
from .e1rm import estimate_e1rm
from .progression import step_down_tier


@dataclass
class LoggedSet:
    """One logged working set paired with its prescription.
    target_rpe feeds e1RM; target_reps_low/high feed the outcome
    classification; feedback_tap feeds both."""
    actual_load: Optional[float] = None
    actual_reps: Optional[int] = None
    feedback_tap: Optional[FeedbackTap] = None
    is_warmup: bool = False
    target_rpe: Optional[float] = None
    target_reps_low: Optional[int] = None
    target_reps_high: Optional[int] = None


@dataclass
class MovementAnalysisInput:
    movement_id: int
    objective: Objective = Objective.MAINTAIN
    current_tier: int = 0
    increment_ladder_len: int = 1
    consecutive_ceiling_sessions: int = 0
    consecutive_failed_progressions: int = 0
    logged_sets: List[LoggedSet] = field(default_factory=list)


@dataclass
class EngineStateInput:
    current_phase: Phase = Phase.CUT
    bodyweight: Optional[float] = None
    cut_to_stab_target: float = 213.0
    cut_to_stab_tolerance: float = 2.0
    rhr_down: bool = False
    sleep_ok: bool = False
    no_rpe_creep: bool = False
    bw_stable_2wk: bool = False
    strength_bounce: bool = False
    subjective_ok: bool = False


@dataclass
class AnalysisContext:
    movements: List[MovementAnalysisInput] = field(default_factory=list)
    engine_state: EngineStateInput = field(default_factory=EngineStateInput)


@dataclass
class MovementStateDelta:
    movement_id: int
    new_e1rm: Optional[float] = None               # None = untouched (no qualifying sets)
    new_tier: Optional[int] = None                 # None = unchanged
    new_consecutive_ceiling: Optional[int] = None  # None = unchanged
    new_consecutive_failed: Optional[int] = None   # None = unchanged
    # No current_load field — the hook NEVER writes current_load (generation's job).


@dataclass
class AnalysisResult:
    movement_deltas: List[MovementStateDelta] = field(default_factory=list)
    phase_transition_available: Optional[Phase] = None  # report-only; applier never writes current_phase


def _best_e1rm_set(logged_sets: List[LoggedSet]) -> Optional[Tuple[LoggedSet, float]]:
    """The anchor set: the e1RM-qualifying working set with the max estimate.
    Returns (set, e1rm) or None if no set qualifies. A qualifying set is a
    non-warmup set with load, reps, feedback_tap, and target_rpe all present."""
    best: Optional[Tuple[LoggedSet, float]] = None
    for s in logged_sets:
        if s.is_warmup:
            continue
        if s.actual_load is None or s.actual_reps is None:
            continue
        if s.feedback_tap is None or s.target_rpe is None:
            continue
        e1rm = estimate_e1rm(s.actual_load, s.actual_reps, s.target_rpe, s.feedback_tap)
        if best is None or e1rm > best[1]:
            best = (s, e1rm)
    return best


def _analyze_movement(mv: MovementAnalysisInput) -> MovementStateDelta:
    delta = MovementStateDelta(movement_id=mv.movement_id)
    anchor = _best_e1rm_set(mv.logged_sets)
    if anchor is None:
        return delta  # no anchor → everything untouched (None)
    anchor_set, anchor_e1rm = anchor
    delta.new_e1rm = anchor_e1rm   # measurement: always-on, objective-independent

    # Prescription machinery is PROGRESS-gated.
    if mv.objective != Objective.PROGRESS:
        return delta

    # Three-way outcome on the anchor set (mutually exclusive).
    is_too_hard = anchor_set.feedback_tap == FeedbackTap.TOO_HARD
    has_range = (anchor_set.target_reps_low is not None
                 and anchor_set.target_reps_high is not None)
    reps = anchor_set.actual_reps  # not None (anchor is a working set)

    if is_too_hard or (has_range and reps < anchor_set.target_reps_low):
        outcome = "MISS"
    elif has_range and reps >= anchor_set.target_reps_high:
        outcome = "CEILING"
    else:
        outcome = "NEITHER"

    if outcome == "CEILING":
        delta.new_consecutive_ceiling = mv.consecutive_ceiling_sessions + 1
        delta.new_consecutive_failed = 0
    elif outcome == "MISS":
        delta.new_consecutive_ceiling = 0
        new_failed = mv.consecutive_failed_progressions + 1
        stepped = step_down_tier(mv.current_tier, mv.increment_ladder_len,
                                 consecutive_fails=new_failed, threshold=2)
        if stepped != mv.current_tier:
            delta.new_tier = stepped
            delta.new_consecutive_failed = 0   # streak consumed by the drop
        else:
            delta.new_consecutive_failed = new_failed
    else:  # NEITHER
        delta.new_consecutive_ceiling = 0
        delta.new_consecutive_failed = 0

    return delta


def analyze_session(ctx: AnalysisContext) -> AnalysisResult:
    """Compute proposed state deltas for a logged session. Pure; never writes."""
    deltas = [_analyze_movement(mv) for mv in ctx.movements]
    # Task 4 will add phase-gate evaluation here.
    return AnalysisResult(movement_deltas=deltas)
