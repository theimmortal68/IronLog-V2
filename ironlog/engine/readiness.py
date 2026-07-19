"""
readiness.py - pure recovery/readiness signal computation.

Computes the six STAB-to-REBUILD gate booleans from plain caller-supplied inputs.
PURE: no DB, no network, and no dependency on readiness persistence rows.
Missing or insufficient data returns False so the gate never passes by silence.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Tuple


BW_WINDOW_DAYS = 14
BW_MIN_READINGS = 10
RHR_WINDOW_DAYS = 7
RHR_MIN_READINGS = 3
RHR_MEAN_DROP_BPM = 3.0
BOOL_WINDOW_DAYS = 10
BOOL_MIN_READINGS = 5
RPE_MIN_READINGS = 3
RPE_CREEP_THRESHOLD = 0.5
STRENGTH_BOUNCE_WINDOW = 6
STRENGTH_BOUNCE_MIN_READINGS = 4
STRENGTH_BOUNCE_WINDOW_DAYS = STRENGTH_BOUNCE_WINDOW * 7
STRENGTH_FLAT_EPSILON_PCT = 0.01
STRENGTH_BOUNCE_MIN_PCT = 0.01


@dataclass
class DailyReadinessInput:
    """One day's readiness data, decoupled from any DB model
    so this module stays testable without a database."""
    date: date
    bodyweight: Optional[float] = None
    body_fat_pct: Optional[float] = None
    resting_hr: Optional[float] = None
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None


def _trailing_rows(
    rows: List[DailyReadinessInput],
    days: int,
    as_of: date,
) -> List[DailyReadinessInput]:
    cutoff = as_of - timedelta(days=days - 1)
    return sorted(
        (row for row in rows if cutoff <= row.date <= as_of),
        key=lambda row: row.date,
    )


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _bool_ratio_ok(values: List[Optional[bool]], min_good_ratio: float) -> bool:
    readings = [value for value in values if value is not None]
    if len(readings) < BOOL_MIN_READINGS:
        return False
    return sum(1 for value in readings if value) >= min_good_ratio * len(readings)


def compute_bw_stable_2wk(
    rows: List[DailyReadinessInput],
    as_of: date,
    tolerance: float = 2.0,
) -> bool:
    """True when at least 10 bodyweight readings in the trailing 14 calendar
    days have a max-min range within tolerance pounds. Fewer readings returns
    False because stability cannot be inferred from sparse data."""
    values = [
        row.bodyweight for row in _trailing_rows(rows, BW_WINDOW_DAYS, as_of)
        if row.bodyweight is not None
    ]
    if len(values) < BW_MIN_READINGS:
        return False
    return max(values) - min(values) <= tolerance


def compute_goal_stable(
    rows: List[DailyReadinessInput],
    as_of: date,
    field: str,
    target: float,
    tolerance: float,
    window_days: int = 7,
    min_readings: int = 4,
) -> bool:
    """True when enough recent readings for field are all at or below target + tolerance.

    This is a sustained-state check: one reading above target + tolerance in
    the as_of-anchored trailing window fails the goal gate, even if every other
    reading is at or below target.
    """
    values = [
        getattr(row, field) for row in _trailing_rows(rows, window_days, as_of)
        if getattr(row, field) is not None
    ]
    if len(values) < min_readings:
        return False
    return all(value <= target + tolerance for value in values)


def compute_rhr_down(
    rows: List[DailyReadinessInput],
    as_of: date,
    baseline: Optional[float],
) -> bool:
    """True when at least 3 resting-HR readings in the trailing 7 calendar days
    average 3+ bpm below baseline. The 3 bpm margin is intentional: smaller
    changes are treated as normal day-to-day noise, not a meaningful drop.
    Baseline must be the athlete's true resting-HR baseline in bpm."""
    if baseline is None:
        return False
    values = [
        row.resting_hr for row in _trailing_rows(rows, RHR_WINDOW_DAYS, as_of)
        if row.resting_hr is not None
    ]
    if len(values) < RHR_MIN_READINGS:
        return False
    return baseline - _mean(values) >= RHR_MEAN_DROP_BPM


def compute_sleep_ok(
    rows: List[DailyReadinessInput],
    as_of: date,
    min_good_ratio: float = 0.7,
) -> bool:
    """True when at least 5 sleep readings in the trailing 10 calendar days are
    present and the True fraction is at least min_good_ratio. Sparse data
    returns False, not an assumed pass."""
    values = [row.sleep_ok for row in _trailing_rows(rows, BOOL_WINDOW_DAYS, as_of)]
    return _bool_ratio_ok(values, min_good_ratio)


def compute_subjective_ok(
    rows: List[DailyReadinessInput],
    as_of: date,
    min_good_ratio: float = 0.7,
) -> bool:
    """True when at least 5 subjective-readiness readings in the trailing 10
    calendar days are present and the True fraction is at least min_good_ratio.
    Sparse data returns False, not an assumed pass."""
    values = [
        row.subjective_ok for row in _trailing_rows(rows, BOOL_WINDOW_DAYS, as_of)
    ]
    return _bool_ratio_ok(values, min_good_ratio)


def compute_no_rpe_creep(recent_rpe_readings: List[float]) -> bool:
    """True when at least 3 oldest-first RPE readings show no sustained climb.
    The trend test compares the earliest third's mean with the latest third's
    mean; an increase of up to 0.5 RPE is allowed as normal noise. Caller must
    pre-filter inputs to an already-recent window because readings carry no
    dates here."""
    if len(recent_rpe_readings) < RPE_MIN_READINGS:
        return False
    slice_len = max(1, len(recent_rpe_readings) // 3)
    early_mean = _mean(recent_rpe_readings[:slice_len])
    late_mean = _mean(recent_rpe_readings[-slice_len:])
    return late_mean - early_mean <= RPE_CREEP_THRESHOLD


def compute_strength_bounce(e1rm_history: List[Tuple[date, float]], as_of: date) -> bool:
    """True when at least 4 oldest-first e1RM points in the trailing 6 entries
    show a bounce: the earlier half is flat-or-declining within a 1% noise band,
    then the later half rises by at least 1%. A lift climbing throughout the
    earlier half is already progressing and returns False. Only points in the
    trailing 6-week window are eligible, which bounds stale histories while
    allowing roughly weekly e1RM observations. The flat-or-declining check is
    endpoint-only: it compares the earlier half's first and last points."""
    cutoff = as_of - timedelta(days=STRENGTH_BOUNCE_WINDOW_DAYS - 1)
    recent_history = sorted(
        (
            (entry_date, value)
            for entry_date, value in e1rm_history
            if cutoff <= entry_date <= as_of
        ),
        key=lambda entry: entry[0],
    )
    if len(recent_history) < STRENGTH_BOUNCE_MIN_READINGS:
        return False
    window = recent_history[-STRENGTH_BOUNCE_WINDOW:]
    values = [value for _, value in window]
    split = len(values) // 2
    previous = values[:split]
    recent = values[split:]
    if len(previous) < 2 or len(recent) < 2:
        return False

    previous_flat_or_declining = (
        previous[-1] <= previous[0] * (1 + STRENGTH_FLAT_EPSILON_PCT)
    )
    recent_bounce = recent[-1] >= recent[0] * (1 + STRENGTH_BOUNCE_MIN_PCT)
    return previous_flat_or_declining and recent_bounce
