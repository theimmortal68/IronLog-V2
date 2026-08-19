from ironlog.engine import round_to_achievable, round_up_to_step, clamp_to_cap, current_increment


def test_round_to_step():
    assert round_to_achievable(47.3, floor=10, step=2.5) == 47.5


def test_respects_floor():
    # single Ares stack can't go below 10
    assert round_to_achievable(7.0, floor=10, step=2.5) == 10


def test_round_up_to_step_exact_multiple_stays_same():
    # already an exact multiple of step -- must not round up further
    assert round_up_to_step(50.0, floor=None, step=5) == 50.0


def test_round_up_to_step_between_multiples_rounds_up_never_down():
    # 92 is between 90 and 95 -- must snap to 95, never 90 (rules out nearest-rounding)
    assert round_up_to_step(92.0, floor=None, step=5) == 95.0
    # even a value just barely over a multiple must round up to the NEXT one
    assert round_up_to_step(90.1, floor=None, step=5) == 95.0


def test_round_up_to_step_respects_floor():
    # ceiling result below floor still clamps up to floor
    assert round_up_to_step(3.0, floor=10, step=5) == 10


def test_clamp_cap():
    assert clamp_to_cap(32.5, 25) == 25      # Landmine Rotation cap
    assert clamp_to_cap(20, 25) == 20
    assert clamp_to_cap(99, None) == 99


def test_current_increment_tier():
    ladder = [10, 5, 2.5]
    assert current_increment(ladder, 0) == 10
    assert current_increment(ladder, 2) == 2.5
    assert current_increment(ladder, 9) == 2.5   # clamps to last rung
