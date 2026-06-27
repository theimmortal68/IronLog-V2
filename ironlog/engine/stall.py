"""
stall.py — pure stall detection (docs/06 §9/§183; v0.5 spec §6).

Two arms: an e1RM-trend arm over a PROGRESS window, and a failed-prescription
arm (the existing consecutive_failed counter). PURE: receives the lift's last
STALL_WINDOW PROGRESS-objective anchor e1RMs (the caller does the PROGRESS-window
selection) and the failed counter; returns both sub-signals plus their union.
No stored flag (ledger precedent — stall is a current-condition recompute).

trend_stalled uses a WHOLE-WINDOW definition: no e1RM in the window exceeds the
window's START by more than STALL_EPSILON_PCT. This catches plateau and decline
but NOT dip-and-recover (e.g. 100->95->102), which an endpoint comparison would
false-flag.
"""

from dataclasses import dataclass
from typing import List

from ..models.enums import Objective

STALL_WINDOW = 3
STALL_MIN_SESSIONS = 3
STALL_EPSILON_PCT = 0.01
STALL_FAILED_THRESHOLD = 2


@dataclass
class StallSignal:
    trend_stalled: bool
    failed_stalled: bool
    stalled: bool  # convenience: trend_stalled or failed_stalled


def detect_stall(
    progress_anchor_e1rms: List[float],
    consecutive_failed: int,
    objective: Objective,
) -> StallSignal:
    """Stall signal for a lift. progress_anchor_e1rms are the anchor e1RMs from
    the lift's last STALL_WINDOW PROGRESS sessions, oldest-first (the caller
    selects them). PROGRESS-gated: a non-PROGRESS lift is never stalled."""
    if objective != Objective.PROGRESS:
        return StallSignal(False, False, False)

    window = progress_anchor_e1rms[-STALL_WINDOW:]
    if len(window) >= STALL_MIN_SESSIONS:
        start = window[0]
        threshold = start * (1 + STALL_EPSILON_PCT)
        trend_stalled = max(window) <= threshold
    else:
        trend_stalled = False  # not enough data

    failed_stalled = consecutive_failed >= STALL_FAILED_THRESHOLD
    return StallSignal(trend_stalled, failed_stalled, trend_stalled or failed_stalled)
