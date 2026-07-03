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
from typing import List, Optional

from ..models.enums import Objective, StallSeverity, StallType

STALL_WINDOW = 3
STALL_MIN_SESSIONS = 3
STALL_EPSILON_PCT = 0.01
STALL_FAILED_THRESHOLD = 2
STALL_FAILED_HIGH_MULT = 2


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


def _window_trend_pct(progress_e1rms: List[float]) -> float:
    """Percent change from the START to the END of the same STALL_WINDOW slice
    detect_stall inspects. Negative == declining, ~0 == flat, positive == rising."""
    window = progress_e1rms[-STALL_WINDOW:]
    if len(window) < 2 or not window[0]:
        return 0.0
    return (window[-1] - window[0]) / window[0] * 100.0


def _is_extended_flat(progress_e1rms: List[float]) -> bool:
    """True when the WHOLE history (not just the last STALL_WINDOW) is flat —
    a longer-running plateau than the base window catches, so it upgrades to
    high severity."""
    if len(progress_e1rms) < STALL_WINDOW * 2:
        return False
    start = progress_e1rms[0]
    if not start:
        return False
    return max(progress_e1rms) <= start * (1 + STALL_EPSILON_PCT)


def build_stall_signal(
    movement_id,
    day_id,
    consecutive_failed: int,
    progress_e1rms: List[float],
    current_load,
    limiting_muscle,
) -> Optional[dict]:
    """Typed stall signal: enriches detect_stall's two boolean arms (failed +
    trend) with a StallType/StallSeverity taxonomy. Thin layer — reuses
    detect_stall for the core stalled/not-stalled decision and the constants
    above for thresholds. Returns None when neither arm fires.

    No `is_swappable` key — that call belongs to the caller, not this signal.
    """
    signal = detect_stall(progress_e1rms, consecutive_failed, Objective.PROGRESS)
    if not signal.stalled:
        return None

    trend_pct = _window_trend_pct(progress_e1rms)
    window = progress_e1rms[-STALL_WINDOW:]

    if consecutive_failed >= STALL_FAILED_THRESHOLD:
        stall_type = StallType.FAILED_PROGRESSION.value
        if consecutive_failed >= STALL_FAILED_THRESHOLD * STALL_FAILED_HIGH_MULT:
            severity = StallSeverity.HIGH.value
        else:
            severity = StallSeverity.LOW.value
        duration_sessions = consecutive_failed
    elif trend_pct < -STALL_EPSILON_PCT * 100.0:
        stall_type = StallType.REGRESSION.value
        if trend_pct <= -STALL_EPSILON_PCT * STALL_FAILED_HIGH_MULT * 100.0:
            severity = StallSeverity.HIGH.value
        else:
            severity = StallSeverity.MEDIUM.value
        duration_sessions = len(window)
    else:
        stall_type = StallType.PLATEAU.value
        if _is_extended_flat(progress_e1rms):
            severity = StallSeverity.HIGH.value
            duration_sessions = len(progress_e1rms)
        else:
            severity = StallSeverity.MEDIUM.value
            duration_sessions = len(window)

    return {
        "movement_id": movement_id,
        "day_id": day_id,
        "stall_type": stall_type,
        "severity": severity,
        "duration_sessions": duration_sessions,
        "current_load": current_load,
        "e1rm_trend": trend_pct,
        "limiting_muscle": limiting_muscle,
    }
