from ironlog.engine import next_set_load
from ironlog.models import FeedbackTap

LADDER = [10, 5, 2.5]


def test_too_easy_goes_up_one_increment():
    # tier 0 -> increment 10
    assert next_set_load(220, FeedbackTap.TOO_EASY, LADDER, 0, 45, 2.5, None) == 230


def test_on_target_holds():
    assert next_set_load(220, FeedbackTap.ON_TARGET, LADDER, 0, 45, 2.5, None) == 220


def test_too_hard_backs_off():
    assert next_set_load(220, FeedbackTap.TOO_HARD, LADDER, 1, 45, 2.5, None) == 215


def test_never_below_floor():
    assert next_set_load(12, FeedbackTap.TOO_HARD, [2.5], 0, 10, 2.5, None) == 10


def test_respects_cap():
    # Landmine-style: cap 25, currently 22.5, "too easy" would push to 25 not 27.5
    assert next_set_load(22.5, FeedbackTap.TOO_EASY, [2.5], 0, None, 2.5, 25) == 25
