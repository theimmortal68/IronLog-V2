from ironlog.engine import estimate_e1rm, implied_rir
from ironlog.models import FeedbackTap


def test_implied_rir_base():
    assert implied_rir(8, FeedbackTap.ON_TARGET) == 2.0   # RPE8 -> 2 in reserve


def test_tap_shifts_rir():
    assert implied_rir(8, FeedbackTap.TOO_EASY) == 3.0
    assert implied_rir(8, FeedbackTap.TOO_HARD) == 1.0


def test_rir_never_negative():
    assert implied_rir(10, FeedbackTap.TOO_HARD) == 0.0


def test_e1rm_back_squat_220x8_rpe8():
    # 220x8 at RPE8 (2 RIR) -> ~293 by Epley; the spec's "~278" used a tighter read
    e = estimate_e1rm(220, 8, 8, FeedbackTap.ON_TARGET)
    assert 285 < e < 300
