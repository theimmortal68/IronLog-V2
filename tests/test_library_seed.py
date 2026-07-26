"""§10 invariants for the 103-movement library seed. The gate that keeps the
mechanical import from drifting to 'wrong data, green tests'."""
import pytest
from sqlmodel import Session, create_engine, select

import ironlog.db as db
from ironlog.models import (Equipment, Movement, Status, Scheme,
                            ProgressionMode, KneeModality)

TOPSET_SIX = {
    "Bench Press [PB]", "Back Squat [PB]", "Front Squat [PB]",
    "Belt Squat [GHR + FT]", "Standing OHP [PB]", "RDL [PB]",
}

# Movement.scheme == TOPSET_BACKOFF is now dead (Task 2 / Phase-1
# reconciliation flipped Belt Squat and RDL to STRAIGHT; the Task 2 review
# fix flipped Bench Press to STRAIGHT too; the fix-topset pass
# (deploy/migrations/015_kill_topset_backoff.sql) flipped the last three
# stragglers — Back Squat, Front Squat, Standing OHP — since Back Squat is
# d2_t1's meso-2 rotation and would otherwise reintroduce the 148.5-class
# fractional-backoff bug. No movement uses TOPSET_BACKOFF anymore.
# rpe_capped is unaffected and stays TOPSET_SIX — see
# tests/test_phase1_reconciliation.py for the flip coverage.


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
    # go-live Task 1 added 2 movements (Face Pull [FT], Reverse Hyper -
    # Single Leg [REV_HYPER]): 108 -> 110.
    # 2026-07-17: +1 for "Reverse Hyper Recovery [REV_HYPER]" (D6's recovery
    # slot got its own movement, no longer sharing "Light Reverse Hyper" with
    # D5's real training day, which has a conflicting progression rule):
    # 110 -> 111.
    # 2026-07-26: +1 Cable Bicep Curl, athlete directive, fills Dips' vacated GS1 slot.
    assert len(_all(seeded)) == 112


def test_status_counts(seeded):
    from collections import Counter
    c = Counter(m.status for m in _all(seeded))
    # go-live Task 1: +2 new ACTIVE movements, +2 ACTIVE-flips (Lat Prayer,
    # Dips) pulled out of INACTIVE: 99 -> 103, 8 -> 6.
    # 2026-07-17: +1 ACTIVE for "Reverse Hyper Recovery [REV_HYPER]": 103 -> 104.
    # 2026-07-26: +1 ACTIVE Cable Bicep Curl, athlete directive, fills Dips' vacated GS1 slot.
    assert c[Status.ACTIVE] == 105
    assert c[Status.INACTIVE] == 6
    assert c[Status.PREP] == 1


def test_topset_backoff_scheme_is_dead(seeded):
    tb = {m.name for m in _all(seeded) if m.scheme == Scheme.TOPSET_BACKOFF}
    assert tb == set(), f"TOPSET_BACKOFF should be unused by every movement: {tb}"


def test_rpe_capped_xor_exempt(seeded):
    for m in _all(seeded):
        assert not (m.rpe_capped and m.rpe_cap_exempt), f"{m.name} is both capped and exempt"


def test_rpe_capped_and_exempt_sets(seeded):
    capped = {m.name for m in _all(seeded) if m.rpe_capped}
    assert capped == TOPSET_SIX, f"rpe_capped set mismatch: {capped}"
    exempt = {m.name for m in _all(seeded) if m.rpe_cap_exempt}
    assert exempt == {"Hip Thrust [HIP_THRUST]", "Banded Hip Thrust [HIP_THRUST]"}, \
        f"rpe_cap_exempt set mismatch: {exempt}"


def test_family_links_resolve(seeded):
    by_id = {m.id: m for m in _all(seeded)}
    for m in _all(seeded):
        if m.derived_from_id is not None:
            assert m.derived_from_id in by_id, f"{m.name} derived_from a missing anchor"
            assert m.start_ratio is not None, f"{m.name} is a variant with no start_ratio"
            # derived_from target must be an anchor (loader contract)
            target = by_id[m.derived_from_id]
            assert target.is_family_anchor, f"{m.name} derived_from non-anchor {target.name}"
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


def test_golive_library_additions(gen_db):
    from ironlog.models.library import Movement
    from ironlog.models.enums import Status
    from sqlmodel import select
    by_name = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    # new movements
    assert "Face Pull [FT]" in by_name
    assert "Reverse Hyper - Single Leg [REV_HYPER]" in by_name
    assert "Cable Bicep Curl [FT]" in by_name
    slscout = by_name["Reverse Hyper - Single Leg [REV_HYPER]"]
    assert slscout.unilateral is True
    # Movement has no `load_code` attribute (that's a seed.py-only dict key
    # consumed into load_equipment_id/equipment_tags at seed time) — assert
    # the persisted equivalent instead.
    assert "REV_HYPER" in slscout.equipment_tags
    # ACTIVE flips (live-programmed movements)
    assert by_name["Lat Prayer [ANDREONI + FT]"].status == Status.ACTIVE
    dips = by_name["Dips [TOWER + TUBES]"]
    assert dips.status == Status.ACTIVE
    assert dips.progression_mode == ProgressionMode.ASSISTED
    assert dips.scheme == Scheme.STRAIGHT
    assert dips.assist_ladder == [40, 30, 18, 9, 0]
    assert "TOWER" in dips.equipment_tags and "TUBES" in dips.equipment_tags


def test_sissy_squat_single_continuous_track(seeded):
    s = next(m for m in _all(seeded) if m.name == "Sissy Squat")
    assert s.knee_modality == KneeModality.SISSY
    assert s.load_floor == 0 and s.load_equipment_id is not None  # increment source present, floored at 0
    assert s.scheme == Scheme.DOUBLE_PROGRESSION
    # exactly one Sissy Squat movement — no plate/DB split
    assert sum(1 for m in _all(seeded) if m.name == "Sissy Squat") == 1


def test_assisted_seed_ladders_for_nordic_family_not_pull_up(seeded):
    by_name = {m.name: m for m in _all(seeded)}

    nordic_ladder = by_name["Nordic Curl [GHR]"].assist_ladder
    assert nordic_ladder == [25, 20, 15, 10, 5, 0]
    assert 20 in nordic_ladder and 25 in nordic_ladder
    assert by_name["Nordic Curl - Volume [GHR]"].assist_ladder == [25, 20, 15, 10, 5, 0]
    assert by_name["Reverse Nordic Curl [GHR]"].assist_ladder == [20, 15, 10, 5, 0]

    assert by_name["Pull-up [TOWER + TUBES]"].assist_ladder is None
