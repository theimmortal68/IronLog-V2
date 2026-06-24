"""
loading.py — turn a desired load into one the equipment can actually make.

The validator and autoregulation both lean on these. A barbell can't make 47.3 lb;
a single Ares stack can't go below 10. Everything routes through here so the engine
never prescribes an impossible load.
"""
from typing import List, Optional


def round_to_achievable(target: float, floor: Optional[float], step: float) -> float:
    """Snap `target` to the nearest reachable load: a multiple of `step`, not
    below `floor`."""
    snapped = round(target / step) * step
    if floor is not None and snapped < floor:
        return floor
    return snapped


def clamp_to_cap(load: float, cap: Optional[float]) -> float:
    """Never exceed a movement's hard cap (e.g. Landmine Rotation 25)."""
    return min(load, cap) if cap is not None else load


def current_increment(ladder: List[float], tier: int) -> float:
    """The active step size for a movement at its current ladder rung."""
    if not ladder:
        return 0.0
    tier = max(0, min(tier, len(ladder) - 1))
    return ladder[tier]
