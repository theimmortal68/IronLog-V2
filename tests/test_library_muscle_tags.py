from ironlog.models.enums import Muscle
from ironlog.seed import MOVEMENTS

_VALID = {m.value for m in Muscle}


def test_every_movement_has_a_valid_primary_muscle():
    missing = [m["name"] for m in MOVEMENTS if not m.get("primary_muscle")]
    assert missing == [], f"untagged: {missing}"
    bad = [m["name"] for m in MOVEMENTS if m["primary_muscle"] not in _VALID]
    assert bad == [], f"invalid primary: {bad}"


def test_secondary_muscles_are_valid_and_listy():
    for m in MOVEMENTS:
        sec = m.get("secondary_muscles", [])
        assert isinstance(sec, list)
        assert all(s in _VALID for s in sec), f"{m['name']}: {sec}"
