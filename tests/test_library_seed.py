"""§10 invariants for the 103-movement library seed. The gate that keeps the
mechanical import from drifting to 'wrong data, green tests'."""
import pytest
from sqlmodel import Session, create_engine, select

import ironlog.db as db
from ironlog.models import (Equipment, Movement, Status, Scheme,
                            ProgressionMode, KneeModality, AssistUnit)

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
    # 2026-07-26: +1 Wide-Grip Pull-up, athlete directive, D4/D6's Pull-up slots switched grips.
    # 2026-07-26: +1 Lying Tricep Extension, athlete directive, fills Pendlay
    # Row Narrow's vacated D1 T2 GS slot after its T1b promotion.
    # 2026-07-26: +1 Pull-up - Neutral Grip (Paused), 3-way pull-up split,
    # D6 no longer shares D4's Wide-Grip Pull-up.
    # 2026-08-10: +3 (STAB maintenance-block redesign, D1 reconciled to
    # already-executed Wk1 reality): Better Fly Standing Lateral Raise [FT],
    # Stryker Pad Seated OHP [DB], Matrix Machine Preacher Curl [EZ]: 115 -> 118.
    # 2026-08-11: +4 (STAB maintenance-block redesign, Task 2, D2 rewritten
    # to match the FINAL doc): Matrix Machine Sissy Squat, Nordic Curl Max
    # [Ares], Hybrid Board Calf Raise [D2], Ab Trainer Decline Sit-up: 118 -> 122.
    # 2026-08-11: +5 (STAB maintenance-block redesign, Task 3, D4 rewritten
    # to match the FINAL doc): Seated BTN OHP [PB], Better Fly Lat Pulldown
    # [FT], Stryker Pad CSR Barbell [PB], Better Fly Cable Pullover [FT],
    # Ab Trainer Hanging Leg Raise: 122 -> 127. (Lying Tricep Extension [SB]
    # is REUSED for D4's T3 tricep slot, not a new row -- no count change.)
    # 2026-08-12: +8 (STAB maintenance-block redesign, Task 4, D5 rewritten
    # to match the FINAL doc, plus a D2 follow-up addendum): Kickstand RDL
    # [DB], Nordic Max Bulgarian Split Squat, Better Fly Kickback [FT],
    # Hybrid Board Calf Raise [D5], Hybrid Board Tib Raise [D5], Better Fly
    # Hip Adduction [FT], Ab Trainer Russian Twist (7, all D5), plus Hybrid
    # Board Tib Raise [D2] (1, D2 follow-up correction replacing Cable Tib
    # Raise): 127 -> 135. (Nordic Curl Max [Ares] is REUSED, same shared
    # Movement row as D2's, not a new row -- no count change.)
    # 2026-08-12: +5 (STAB maintenance-block redesign, Task 5, D6 rewritten
    # to match the FINAL doc): Better Fly Cable Bicep Curl [FT], Stryker Pad
    # CSR Cables [FT], Better Fly Rear Delt Extension [FT], Better Fly OH
    # Tricep Extension [FT], AbMat Ab Bench Pad Cable Crunch [FT]: 135 -> 140.
    # (Swiss Bar CG Press [SB] is REUSED for D6's new close-grip-bench slot,
    # not a new row -- no count change. Dips [TOWER + TUBES] reverted from
    # ASSISTED to LADDER, same Movement row -- no count change either.)
    # 2026-08-12 (STAB redesign fix, post-Task-5): +1 "Wide-Grip Pull-up
    # [TOWER + TUBES]" (D6's new ASSISTED wide-grip pull-up, per
    # docs/superpowers/specs/2026-08-10-stab-maintenance-block-redesign-
    # design.md §5): 140 -> 141.
    # 2026-08-13: +1 "Better Fly Sagittal Lat Pulldown [FT]" (D1's T3 GS,
    # replaces Lat Prayer [ANDREONI + FT], athlete directive -- Andreoni
    # lat-prayer motion isn't reproducible on the Better Fly cuff): 141 -> 142.
    # 2026-08-14: +1 "Matrix Machine Bulgarian Split Squat" (D5's T2 GS,
    # replaces Nordic Max Bulgarian Split Squat, athlete directive -- Nordic
    # Max rig conflict with Nordic Curl Max in the same giant set): 142 -> 143.
    # 2026-08-16: +1 "D-Handle Cable Bicep Curl [FT]" (D6's GS2, replaces
    # Better Fly Cable Bicep Curl [FT], athlete directive -- Better Fly cuff
    # doesn't work well for curls): 143 -> 144.
    # 2026-08-20: +2 "Nordic Curl Max [Apex]" (D2's WeekParityRotation "A"
    # week, angle-adjustable unassisted Nordic) and "Lying Leg Curl [GHR +
    # Ares]" (D5's new T2 GS movement, replaces Nordic Curl Max [Ares] --
    # D5 no longer has a Nordic slot at all): 144 -> 146.
    # 2026-08-29: +1 "Seated Leg Extension [GHR + FT]" (D6's GS3, new fresh
    # slot d6_g3f, athlete directive): 146 -> 147.
    # 2026-08-29: +1 "Kickstand RDL [PB]" (D5's T1 anchor repointed from the
    # DB variant to a barbell, athlete directive; old DB row stays ACTIVE,
    # unwired): 147 -> 148.
    # 2026-09-01: +1 "Cable Serratus Punch/Reach [FT]" (D6's GS1, replaces
    # Seated Leg Extension per outside-review reconciliation, athlete
    # directive; old row stays ACTIVE, unwired): 148 -> 149.
    # 2026-09-01: +1 "Suitcase Dreadmill Carry" (D2 T3 GS, fresh slot
    # d2_t3f, spec 59; unwired in the fresh-seed universe, live-only wiring
    # via migration 063): 149 -> 150.
    assert len(_all(seeded)) == 150


def test_status_counts(seeded):
    from collections import Counter
    c = Counter(m.status for m in _all(seeded))
    # go-live Task 1: +2 new ACTIVE movements, +2 ACTIVE-flips (Lat Prayer,
    # Dips) pulled out of INACTIVE: 99 -> 103, 8 -> 6.
    # 2026-07-17: +1 ACTIVE for "Reverse Hyper Recovery [REV_HYPER]": 103 -> 104.
    # 2026-07-26: +1 ACTIVE Cable Bicep Curl, athlete directive, fills Dips' vacated GS1 slot.
    # 2026-07-26: +1 ACTIVE Wide-Grip Pull-up, athlete directive, D4/D6 grip switch.
    # 2026-07-26: +1 ACTIVE Lying Tricep Extension, fills Pendlay Row's vacated D1 slot.
    # 2026-07-26: +1 ACTIVE Pull-up - Neutral Grip (Paused), 3-way pull-up split.
    # 2026-08-10: +3 ACTIVE (STAB redesign): Better Fly Standing Lateral
    # Raise, Stryker Pad Seated OHP, Matrix Machine Preacher Curl: 108 -> 111.
    # 2026-08-11: +4 ACTIVE (STAB redesign, Task 2): Matrix Machine Sissy
    # Squat, Nordic Curl Max [Ares], Hybrid Board Calf Raise [D2], Ab Trainer
    # Decline Sit-up: 111 -> 115.
    # 2026-08-11: +5 ACTIVE (STAB redesign, Task 3): Seated BTN OHP [PB],
    # Better Fly Lat Pulldown [FT], Stryker Pad CSR Barbell [PB], Better Fly
    # Cable Pullover [FT], Ab Trainer Hanging Leg Raise: 115 -> 120.
    # 2026-08-12: +8 ACTIVE (STAB redesign, Task 4 + D2 addendum): 120 -> 128.
    # 2026-08-12: +5 ACTIVE (STAB redesign, Task 5, D6): Better Fly Cable
    # Bicep Curl, Stryker Pad CSR Cables, Better Fly Rear Delt Extension,
    # Better Fly OH Tricep Extension, AbMat Ab Bench Pad Cable Crunch: 128 -> 133.
    # 2026-08-12: +1 ACTIVE (STAB redesign fix, post-Task-5): Wide-Grip
    # Pull-up [TOWER + TUBES]: 133 -> 134.
    # 2026-08-13: +1 ACTIVE Better Fly Sagittal Lat Pulldown [FT] (D1 T3 GS,
    # replaces Lat Prayer, athlete directive): 134 -> 135.
    # 2026-08-14: +1 ACTIVE Matrix Machine Bulgarian Split Squat (D5 T2 GS,
    # replaces Nordic Max Bulgarian Split Squat, athlete directive): 135 -> 136.
    # 2026-08-16: +1 ACTIVE D-Handle Cable Bicep Curl [FT] (D6 GS2, replaces
    # Better Fly Cable Bicep Curl, athlete directive): 136 -> 137.
    # 2026-08-20: +2 ACTIVE Nordic Curl Max [Apex] and Lying Leg Curl [GHR +
    # Ares]: 137 -> 139.
    # 2026-08-29: +1 ACTIVE Seated Leg Extension [GHR + FT] (D6 GS3, fresh
    # slot d6_g3f, athlete directive): 139 -> 140.
    # 2026-08-29: +1 ACTIVE Kickstand RDL [PB] (D5 T1 anchor repointed from
    # the DB variant to a barbell, athlete directive): 140 -> 141.
    # 2026-09-01: +1 ACTIVE Cable Serratus Punch/Reach [FT] (D6 GS1, replaces
    # Seated Leg Extension, athlete directive): 141 -> 142.
    # 2026-09-01: +1 ACTIVE Suitcase Dreadmill Carry (D2 T3 GS, spec 59):
    # 142 -> 143.
    assert c[Status.ACTIVE] == 143
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
    # 2026-08-12 (STAB maintenance-block redesign, Task 5): Dips reverted
    # from bodyweight+band-assist (ASSISTED/STRAIGHT) back to cable-loaded
    # (LADDER/DOUBLE_PROGRESSION) -- see ironlog/seed.py's Dips comment.
    #
    # 2026-08-16 (athlete directive): converted BACK to band assist (2nd
    # flip) -- real stackable-band setup (green/purple/black, combined
    # mid-session), modeled as a plain CABLE_LB assist value, not the old
    # discrete band-count ladder from the 2026-07-26 experiment.
    #
    # 2026-08-31 (athlete directive, 3rd flip -- misclassification fix): the
    # real bands add resistance, they don't assist -- ASSISTED/CABLE_LB had
    # the progression direction backwards. Reverts to LADDER/
    # DOUBLE_PROGRESSION (see ironlog/seed.py's Dips comment). assist_ladder/
    # assist_unit stay on the row for historical reference, unused now.
    assert dips.progression_mode == ProgressionMode.LADDER
    assert dips.scheme == Scheme.DOUBLE_PROGRESSION
    assert dips.assist_ladder == [120, 110, 100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 0]
    assert dips.assist_unit == AssistUnit.CABLE_LB
    assert "TOWER" in dips.equipment_tags and "TUBES" in dips.equipment_tags


def test_sissy_squat_single_continuous_track(seeded):
    s = next(m for m in _all(seeded) if m.name == "Sissy Squat")
    assert s.knee_modality == KneeModality.SISSY
    assert s.load_floor == 0 and s.load_equipment_id is not None  # increment source present, floored at 0
    assert s.scheme == Scheme.DOUBLE_PROGRESSION
    # exactly one Sissy Squat movement — no plate/DB split
    assert sum(1 for m in _all(seeded) if m.name == "Sissy Squat") == 1


def test_assisted_seed_ladders_for_nordic_family_and_pull_up(seeded):
    by_name = {m.name: m for m in _all(seeded)}

    nordic_ladder = by_name["Nordic Curl [GHR]"].assist_ladder
    assert nordic_ladder == [25, 20, 15, 10, 5, 0]
    assert 20 in nordic_ladder and 25 in nordic_ladder
    assert by_name["Nordic Curl - Volume [GHR]"].assist_ladder == [25, 20, 15, 10, 5, 0]
    assert by_name["Reverse Nordic Curl [GHR]"].assist_ladder == [20, 15, 10, 5, 0]

    # 2026-07-26: real assist ladder populated (3 stacked 20lb bands, athlete
    # directive) -- drop a band at 3x12; ASSISTANCE_REDUCTION now drives it.
    assert by_name["Pull-up [TOWER + TUBES]"].assist_ladder == [60, 40, 20, 0]
