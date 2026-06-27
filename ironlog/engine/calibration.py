"""
calibration.py — pure calibration-flip (docs/02; v0.5 spec §5).

A lift graduates CALIBRATING -> MEASURED when two consecutive weekly e1RM
estimates agree within CALIBRATION_AGREEMENT_PCT. PURE: receives pre-bucketed
weekly estimates (the caller aggregates session anchor e1RMs per week by max);
no rows, no dates, no calendar math. One-way: only fires from CALIBRATING.
The flip is fully reconstructable from the history rows + the WeekKeys that
defined the aggregation.
"""

from typing import List

from ..models.enums import CalibrationStatus

CALIBRATION_AGREEMENT_PCT = 0.05


def evaluate_calibration_flip(
    weekly_estimates: List[float],
    current_status: CalibrationStatus,
) -> bool:
    """True iff the lift should flip to MEASURED: currently CALIBRATING, at
    least two weekly estimates, and the LAST TWO agree within 5%.
    Thin data (0 or 1 estimates) -> False."""
    if current_status != CalibrationStatus.CALIBRATING:
        return False
    if len(weekly_estimates) < 2:
        return False
    a, b = weekly_estimates[-2], weekly_estimates[-1]
    denom = max(a, b)
    if denom <= 0:
        return False
    return abs(a - b) / denom <= CALIBRATION_AGREEMENT_PCT
