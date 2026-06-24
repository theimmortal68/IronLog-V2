"""
autoregulate.py — the between-set loop (deterministic).

After a working set is logged, the tap says whether the load was right. This nudges
the *next* set, grid-aligned to the equipment and clamped to floor and cap. No LLM:
this is pure, instant, and can't drift.
"""
from typing import List, Optional

from ..models.enums import FeedbackTap
from .loading import clamp_to_cap, current_increment, round_to_achievable


def next_set_load(current_load: float, tap: FeedbackTap, ladder: List[float],
                  tier: int, floor: Optional[float], step: float,
                  cap: Optional[float]) -> float:
    """Suggest the next set's load from the tap.

    TOO_EASY  -> up one increment (within cap)
    ON_TARGET -> hold
    TOO_HARD  -> down one increment (not below floor)
    """
    delta_unit = current_increment(ladder, tier) or step
    if tap == FeedbackTap.TOO_EASY:
        proposed = current_load + delta_unit
    elif tap == FeedbackTap.TOO_HARD:
        proposed = current_load - delta_unit
    else:
        proposed = current_load
    proposed = round_to_achievable(proposed, floor, step)
    return clamp_to_cap(proposed, cap)
