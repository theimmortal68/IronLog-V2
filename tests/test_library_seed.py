"""§10 invariants for the 103-movement library seed. The gate that keeps the
mechanical import from drifting to 'wrong data, green tests'."""
import pytest
from sqlmodel import Session, create_engine, select, SQLModel

import ironlog.db as db
from ironlog.models import (Equipment, Movement, Status, Scheme,
                            ProgressionMode, KneeModality)

TOPSET_SIX = {
    "Bench Press [PB]", "Back Squat [PB]", "Front Squat [PB]",
    "Belt Squat [GHR + FT]", "Standing OHP [PB]", "RDL [PB]",
}


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


def test_total_count_103(seeded):
    assert len(_all(seeded)) == 103


def test_status_counts(seeded):
    from collections import Counter
    c = Counter(m.status for m in _all(seeded))
    assert c[Status.ACTIVE] == 94
    assert c[Status.INACTIVE] == 8
    assert c[Status.PREP] == 1


def test_topset_backoff_is_exactly_the_six(seeded):
    tb = {m.name for m in _all(seeded) if m.scheme == Scheme.TOPSET_BACKOFF}
    assert tb == TOPSET_SIX


def test_rpe_capped_xor_exempt(seeded):
    for m in _all(seeded):
        assert not (m.rpe_capped and m.rpe_cap_exempt), f"{m.name} is both capped and exempt"


def test_family_links_resolve(seeded):
    by_id = {m.id: m for m in _all(seeded)}
    for m in _all(seeded):
        if m.derived_from_id is not None:
            assert m.derived_from_id in by_id, f"{m.name} derived_from a missing anchor"
            assert m.start_ratio is not None, f"{m.name} is a variant with no start_ratio"
        if m.is_family_anchor:
            assert m.family is not None, f"{m.name} is an anchor with no family"


def test_every_required_knee_modality_has_an_active_movement(seeded):
    active_mods = {m.knee_modality for m in _all(seeded)
                   if m.status == Status.ACTIVE and m.knee_modality is not None}
    for required in (KneeModality.NORDIC, KneeModality.TIB,
                     KneeModality.SISSY, KneeModality.KOT):
        assert required in active_mods, f"no ACTIVE movement for {required} (docs/06 §4 unsatisfiable)"


def test_load_equipment_ids_resolve(seeded):
    eq_ids = {e.id for e in seeded.exec(select(Equipment)).all()}
    for m in _all(seeded):
        if m.load_equipment_id is not None:
            assert m.load_equipment_id in eq_ids, f"{m.name} load_equipment_id dangling"


def test_load_progression_has_increment_source(seeded):
    # CROSS-FIELD: a movement that progresses load must have a resolvable
    # increment source (min_step, via equipment or movement-level) + load_floor.
    eq_by_id = {e.id: e for e in seeded.exec(select(Equipment)).all()}
    LOAD_SCHEMES = {Scheme.DOUBLE_PROGRESSION, Scheme.TOPSET_BACKOFF}
    for m in _all(seeded):
        progresses_load = m.scheme in LOAD_SCHEMES or m.progression_mode == ProgressionMode.LADDER
        if not progresses_load:
            continue
        eq = eq_by_id.get(m.load_equipment_id) if m.load_equipment_id else None
        has_step = (m.min_step is not None) or (eq is not None and eq.min_step is not None)
        assert has_step, f"{m.name} progresses load but has no increment source"
        assert m.load_floor is not None, f"{m.name} progresses load but has no load_floor"


def test_sissy_squat_single_continuous_track(seeded):
    s = next(m for m in _all(seeded) if m.name == "Sissy Squat")
    assert s.knee_modality == KneeModality.SISSY
    assert s.load_floor == 0 and s.load_equipment_id is not None  # increment source present, floored at 0
    assert s.scheme == Scheme.DOUBLE_PROGRESSION
    # exactly one Sissy Squat movement — no plate/DB split
    assert sum(1 for m in _all(seeded) if m.name == "Sissy Squat") == 1
