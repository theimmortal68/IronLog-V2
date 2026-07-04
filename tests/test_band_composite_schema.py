"""Task 1 — HT band-composite schema + inventory gate.

Covers the new JSON band-config fields (MovementState.ht_band_config,
PlannedSet.band_config) and the BandPair inventory reseed from the corrected
rest/peak formula (bottom_lb = rated/side x2, peak_lb = rated/side x5).

NO from __future__ import annotations (project-wide constraint).
"""
import pytest
from sqlmodel import Session, create_engine, select

from ironlog.models.enums import SetRole
from ironlog.models.library import BandPair, MovementState
from ironlog.models.session import PlannedSet


def test_new_json_config_fields_exist():
    ms = MovementState(movement_id=1, day_id="d2", ht_band_config=[0, 1])
    assert ms.ht_band_config == [0, 1]

    ps = PlannedSet(
        planned_exercise_id=1, set_index=0, set_role=SetRole.WORKING,
        band_config=[0],
    )
    assert ps.band_config == [0]


@pytest.fixture(scope="module")
def seeded_db():
    eng = create_engine("sqlite://")
    import ironlog.db as db
    db.engine = eng
    import importlib, ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()
    with Session(eng) as s:
        yield s


def test_band_inventory_seeded_from_formula(seeded_db):
    bands = {b.label: (b.bottom_lb, b.peak_lb) for b in seeded_db.exec(select(BandPair)).all()}
    # rest = rated/side x2, peak = rated/side x5
    assert bands["#0 Orange"] == (18, 45)
    assert bands["#1 Red"] == (36, 90)
    assert bands["#2 Blue"] == (60, 150)
    assert bands["#3 Green"] == (80, 200)
    assert bands["#4 Black"] == (130, 325)
    assert bands["#5 Purple"] == (190, 475)
    assert all(b.usable for b in seeded_db.exec(select(BandPair)).all())
