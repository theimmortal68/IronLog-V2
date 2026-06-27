"""Tests for engine.stall — pure stall detection. The dip-and-recover case is
the keystone: it passes a naive monotonic test but fails on real noisy e1RM."""
from ironlog.engine.stall import detect_stall, StallSignal
from ironlog.models.enums import Objective

PROGRESS = Objective.PROGRESS
MAINTAIN = Objective.MAINTAIN


def test_dip_and_recover_not_trend_stalled():
    # 100 -> 95 -> 102: recovers above the window start by >1% -> NOT stalled (KEYSTONE)
    sig = detect_stall([100.0, 95.0, 102.0], 0, PROGRESS)
    assert sig.trend_stalled is False
    assert sig.stalled is False


def test_plateau_trend_stalled():
    # flat within epsilon -> stalled
    sig = detect_stall([100.0, 100.0, 100.5], 0, PROGRESS)
    assert sig.trend_stalled is True
    assert sig.stalled is True


def test_decline_trend_stalled():
    sig = detect_stall([100.0, 98.0, 96.0], 0, PROGRESS)
    assert sig.trend_stalled is True


def test_monotonic_climb_not_stalled():
    sig = detect_stall([100.0, 103.0, 106.0], 0, PROGRESS)
    assert sig.trend_stalled is False


def test_fewer_than_min_sessions_not_trend_stalled():
    assert detect_stall([100.0, 100.0], 0, PROGRESS).trend_stalled is False
    assert detect_stall([100.0], 0, PROGRESS).trend_stalled is False
    assert detect_stall([], 0, PROGRESS).trend_stalled is False


def test_failed_stalled_at_threshold():
    # climbing e1RM (not trend-stalled) but 2 failed prescriptions -> failed_stalled
    sig = detect_stall([100.0, 103.0, 106.0], 2, PROGRESS)
    assert sig.trend_stalled is False
    assert sig.failed_stalled is True
    assert sig.stalled is True


def test_failed_below_threshold_not_failed_stalled():
    sig = detect_stall([100.0, 103.0, 106.0], 1, PROGRESS)
    assert sig.failed_stalled is False


def test_stalled_is_union():
    # trend stalled OR failed stalled
    assert detect_stall([100.0, 100.0, 100.0], 0, PROGRESS).stalled is True   # trend only
    assert detect_stall([100.0, 103.0, 106.0], 2, PROGRESS).stalled is True   # failed only


def test_non_progress_objective_all_false():
    # a maintained lift is never stalled, even with flat e1RM + failures
    sig = detect_stall([100.0, 100.0, 100.0], 5, MAINTAIN)
    assert sig.trend_stalled is False
    assert sig.failed_stalled is False
    assert sig.stalled is False
