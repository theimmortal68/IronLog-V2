"""Test that the 3 program-required movements are present and correctly configured."""
from ironlog.models.library import Movement
from ironlog.models.enums import ProgressionMode, Scheme, Region
from sqlmodel import select
import pytest
from sqlmodel import Session, create_engine

import ironlog.db as db


ADDED = ["Dragon Flag", "Face-Up Incline Knee Raise", "Andreoni Cable Pullover"]


@pytest.fixture(scope="module")
def seeded():
    eng = create_engine("sqlite://")
    db.engine = eng
    import importlib, ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()
    with Session(eng) as s:
        yield s


def _all(s):
    return s.exec(select(Movement)).all()


def test_added_movements_present_and_active(seeded):
    names = {m.name for m in _all(seeded)}
    for n in ADDED:
        assert n in names, f"missing program-required movement: {n}"


def test_added_movements_fields(seeded):
    by = {m.name: m for m in _all(seeded)}
    assert by["Dragon Flag"].progression_mode == ProgressionMode.PROTOCOL
    assert by["Dragon Flag"].region == Region.CORE
    # Fix C: bodyweight/incline, not a lb load — ASSISTED (assist_level degrees),
    # mirroring Nordic Curl [GHR]. Was mis-typed LADDER (read current_load in lb).
    assert by["Face-Up Incline Knee Raise"].progression_mode == ProgressionMode.ASSISTED
    assert by["Face-Up Incline Knee Raise"].assist_ladder == [25, 20, 15, 10, 5, 0]
    assert by["Face-Up Incline Knee Raise"].region == Region.CORE
    assert by["Andreoni Cable Pullover"].scheme == Scheme.DOUBLE_PROGRESSION
    assert by["Andreoni Cable Pullover"].progression_mode == ProgressionMode.LADDER
    assert by["Andreoni Cable Pullover"].region == Region.UPPER
    # DOUBLE_PROGRESSION movement must have a load source (cross-field §10 invariant)
    assert by["Andreoni Cable Pullover"].load_equipment_id is not None
