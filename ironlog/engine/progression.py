"""
progression.py — objective resolution and increment-tier logic (deterministic).

These encode the progression-model spec's runtime contract and the tier rules.
"""
from typing import Optional

from ..models.enums import Objective


def resolve_objective(objective_override: Optional[Objective],
                      phase_default: Objective) -> Objective:
    """A movement's objective: its own override, else the phase default.
    This single line is what lets pull-ups PROGRESS while primaries MAINTAIN."""
    return objective_override or phase_default


def should_attempt_progression(objective: Objective,
                               phase_progression_attempted: bool) -> bool:
    return objective == Objective.PROGRESS or phase_progression_attempted


def step_down_tier(tier: int, ladder_len: int, consecutive_fails: int,
                   threshold: int = 2) -> int:
    """Drop a rung after `threshold` consecutive failed progressions."""
    if consecutive_fails >= threshold and tier < ladder_len - 1:
        return tier + 1
    return tier


def reset_tier_on_rebuild() -> int:
    """REBUILD entry restores capacity -> back to the coarse rung."""
    return 0


def maybe_reset_tier_on_breakthrough(tier: int, e1rm_gain: float,
                                     coarse_step: float) -> int:
    """A real strength gain (>= one coarse increment) lets the lift take bigger
    steps again -> up one rung. Never triggered by a mere deload."""
    if e1rm_gain >= coarse_step and tier > 0:
        return tier - 1
    return tier
