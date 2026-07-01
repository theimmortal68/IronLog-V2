from ironlog.models.enums import Muscle
from ironlog.models.library import Movement


def test_movement_has_muscle_fields_defaulting_empty():
    m = Movement(name="X [DB]", base_name="X")
    assert m.primary_muscle is None
    assert m.secondary_muscles == []


def test_muscle_enum_has_expected_members():
    expected = {
        "UPPER_CHEST", "MID_LOWER_CHEST", "LATS", "MID_BACK", "UPPER_TRAPS",
        "FRONT_DELT", "SIDE_DELT", "REAR_DELT", "BICEPS", "TRICEPS", "FOREARMS",
        "QUADS", "HAMSTRINGS", "GLUTES", "ADDUCTORS", "CALVES", "ABS",
        "SPINAL_ERECTORS", "TIBIALIS", "ROTATOR_CUFF",
    }
    assert {m.value for m in Muscle} == expected
