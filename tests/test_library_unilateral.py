from ironlog.models.library import Movement


def test_movement_has_unilateral_defaulting_false():
    m = Movement(name="X [DB]", base_name="X")
    assert m.unilateral is False
