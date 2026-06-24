"""
e1rm.py — estimate a one-rep max from a submaximal set.

Pure functions, no database — easy to unit-test. This is the math the calibration
block uses: a set taken at a known RPE target with a feedback tap implies how many
reps were left in reserve, and Epley turns that into an e1RM.
"""
from ..models.enums import FeedbackTap


def implied_rir(target_rpe: float, tap: FeedbackTap) -> float:
    """Reps-in-reserve implied by hitting (or missing) the RPE target.

    Base RIR = 10 - target_rpe  (RPE 8 -> 2 in reserve).
    The tap adjusts it relative to target: easier than expected -> more reserve.
    """
    base = 10.0 - target_rpe
    adjust = {FeedbackTap.TOO_EASY: +1.0,
              FeedbackTap.ON_TARGET: 0.0,
              FeedbackTap.TOO_HARD: -1.0}[tap]
    return max(0.0, base + adjust)


def epley_e1rm(load: float, reps: int, rir: float) -> float:
    """Epley estimate using reps-to-failure = reps + reps-in-reserve."""
    reps_to_failure = reps + rir
    return load * (1.0 + reps_to_failure / 30.0)


def estimate_e1rm(load: float, reps: int, target_rpe: float, tap: FeedbackTap) -> float:
    """Convenience: e1RM straight from a logged working set."""
    return epley_e1rm(load, reps, implied_rir(target_rpe, tap))
