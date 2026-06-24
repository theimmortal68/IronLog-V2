from ironlog.engine import round_to_achievable, clamp_to_cap, current_increment


def test_round_to_step():
    assert round_to_achievable(47.3, floor=10, step=2.5) == 47.5


def test_respects_floor():
    # single Ares stack can't go below 10
    assert round_to_achievable(7.0, floor=10, step=2.5) == 10


def test_clamp_cap():
    assert clamp_to_cap(32.5, 25) == 25      # Landmine Rotation cap
    assert clamp_to_cap(20, 25) == 20
    assert clamp_to_cap(99, None) == 99


def test_current_increment_tier():
    ladder = [10, 5, 2.5]
    assert current_increment(ladder, 0) == 10
    assert current_increment(ladder, 2) == 2.5
    assert current_increment(ladder, 9) == 2.5   # clamps to last rung
