"""Ramp-set generation for heavy barbell anchor lifts.

NO from __future__ import annotations (project-wide constraint).
"""
import pytest
from sqlmodel import select

from ironlog.engine.loading import round_to_achievable, round_up_to_step
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import SetRole
from ironlog.models.library import Movement, MovementState


RAMP_ELIGIBLE_NAMES = {
    "Bench Press [PB]",
    "Belt Squat [GHR + FT]",
    "Back Squat [PB]",
    # The authoritative YAML maps both rdl_d5 and rdl_conventional to this row.
    "RDL [PB]",
    "Staggered RDL [PB]",
    # 2026-08-13: D4's T1 anchor since the STAB redesign (Task 3, 2026-08-11) --
    # plate-loaded barbell press, missed when it replaced Standing OHP [PB].
    "Seated BTN OHP [PB]",
    # 2026-08-29: D5's T1 anchor, repointed from the DB variant to a barbell
    # (athlete directive) -- same recurring omission class as Seated BTN OHP.
    "Kickstand RDL [PB]",
}


def _canned_for(sk, ctx):
    slots = []
    for slot in sk.adaptive_slots:
        if slot.kind in ("giant", "knee"):
            slots.append(SlotSelection(slot.slot_id, ctx.candidate_menus[slot.slot_id][0]))
    return Selections(ordering=[slot.slot_id for slot in slots], slots=slots, rationale="t")


def _assemble(day_role, db, meso_number=1):
    week_key = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton(day_role, db, meso_number=meso_number)
    ctx = resolve_context(day_role, sk, db, week_key)
    return assemble(_canned_for(sk, ctx), sk, ctx, db)


def _movement(db, name):
    return db.exec(select(Movement).where(Movement.name == name)).one()


def _exercise_for(assembled, movement_id):
    for group in assembled.session.groups:
        for exercise in group.exercises:
            if exercise.movement_id == movement_id:
                return exercise
    raise AssertionError(f"movement_id {movement_id} not found in assembled session")


def test_seed_marks_only_explicit_ramp_eligible_movements(gen_db):
    movements = gen_db.exec(select(Movement)).all()
    actual = {movement.name for movement in movements if movement.ramp_eligible}
    assert actual == RAMP_ELIGIBLE_NAMES


def test_d2_belt_squat_anchor_gets_three_ramp_sets_before_working_sets(gen_db_calibrated):
    assembled = _assemble("D2 Lower A", gen_db_calibrated)
    belt = _movement(gen_db_calibrated, "Belt Squat [GHR + FT]")
    exercise = _exercise_for(assembled, belt.id)

    sets = exercise.planned_sets
    assert [planned_set.set_index for planned_set in sets] == [-3, -2, -1, 0, 1, 2]

    ramps = sets[:3]
    working = sets[3:]
    assert [planned_set.set_role for planned_set in ramps] == [SetRole.RAMP] * 3
    assert [planned_set.is_warmup for planned_set in ramps] == [True, True, True]
    assert [planned_set.target_reps_low for planned_set in ramps] == [5, 3, 2]
    assert [planned_set.target_reps_high for planned_set in ramps] == [5, 3, 2]
    assert [planned_set.target_rpe for planned_set in ramps] == [None, None, None]

    working_load = working[0].target_load
    assert working_load is not None
    assert [planned_set.target_load for planned_set in working] == [working_load] * 3
    # Ramp sets always round UP to a clean 5lb step -- NOT belt.min_step (2.5) and
    # NOT nearest-rounding. See test_bench_ramp_sets_round_up_to_5_not_movement_own_step
    # below for a case where this diverges numerically from the old nearest-2.5 behavior.
    expected_loads = [
        round_up_to_step(working_load * pct, belt.load_floor, 5)
        for pct in (0.4, 0.6, 0.8)
    ]
    assert [planned_set.target_load for planned_set in ramps] == pytest.approx(expected_loads)


def test_bench_ramp_sets_round_up_to_5_not_movement_own_step(gen_db_calibrated):
    """Athlete directive: ramp sets always round UP to the nearest 5lb, regardless
    of the movement's own working-set increment. Bench Press [PB]'s own min_step
    is 2.5 -- pick a working load (185) where load*0.6 and load*0.8 land on values
    that are NOT multiples of 5, so the old nearest-2.5 rounding and the new
    round-up-to-5 rounding produce genuinely different numbers:
      - load*0.6 = 111  -> old (nearest 2.5): 110   | new (up to 5): 115
      - load*0.8 = 148  -> old (nearest 2.5): 147.5  | new (up to 5): 150
    """
    bench = _movement(gen_db_calibrated, "Bench Press [PB]")
    state = gen_db_calibrated.exec(
        select(MovementState).where(MovementState.movement_id == bench.id)
    ).one()
    state.current_load = 185.0
    gen_db_calibrated.add(state)
    gen_db_calibrated.commit()

    assembled = _assemble("D1 Upper Push", gen_db_calibrated)
    exercise = _exercise_for(assembled, bench.id)

    sets = exercise.planned_sets
    ramps = sets[:3]
    working = sets[3:]

    working_load = working[0].target_load
    assert working_load == 185.0

    # Sanity: prove these expected values are NOT what old nearest-to-2.5
    # rounding would have produced, so this test can't pass by coincidence.
    old_behavior_loads = [
        round_to_achievable(working_load * pct, bench.load_floor, bench.min_step)
        for pct in (0.4, 0.6, 0.8)
    ]
    assert old_behavior_loads == [75.0, 110.0, 147.5]

    expected_loads = [round_up_to_step(working_load * pct, bench.load_floor, 5) for pct in (0.4, 0.6, 0.8)]
    assert expected_loads == [75.0, 115.0, 150.0]
    assert [planned_set.target_load for planned_set in ramps] == pytest.approx(expected_loads)
    assert expected_loads != old_behavior_loads


def test_non_ramp_eligible_pullup_anchor_gets_no_ramp_sets(gen_db_calibrated):
    # 2026-07-26: D4's Pull-up slot switched to "Wide-Grip Pull-up [TOWER]"
    # (athlete directive) -- "Pull-up [TOWER + TUBES]" is now D1-only (still
    # assisted/banded, unaffected by this test's point about non-ramp anchors).
    #
    # 2026-08-11 (STAB maintenance-block redesign, Task 3): D4's T1b anchor
    # itself turned over again -- Wide-Grip Pull-up [TOWER] -> Better Fly Lat
    # Pulldown [FT] (athlete directive, drops D4's direct pull-up work).
    # Retargeted to the new anchor; it's likewise NOT in RAMP_ELIGIBLE_NAMES
    # (cable double-progression, not a heavy barbell lift), so the test's
    # point -- a non-ramp-eligible anchor gets plain working sets, no ramp --
    # is unaffected by which specific movement occupies the slot.
    assembled = _assemble("D4 Upper Pull", gen_db_calibrated)
    pullup = _movement(gen_db_calibrated, "Better Fly Lat Pulldown [FT]")
    exercise = _exercise_for(assembled, pullup.id)

    assert [planned_set.set_role for planned_set in exercise.planned_sets] == [
        SetRole.WORKING,
        SetRole.WORKING,
        SetRole.WORKING,
    ]
    assert not any(planned_set.is_warmup for planned_set in exercise.planned_sets)


def test_ramp_eligible_anchor_without_load_gets_no_ramp_sets(gen_db):
    assembled = _assemble("D2 Lower A", gen_db)
    belt = _movement(gen_db, "Belt Squat [GHR + FT]")
    exercise = _exercise_for(assembled, belt.id)

    assert [planned_set.set_role for planned_set in exercise.planned_sets] == [
        SetRole.WORKING,
        SetRole.WORKING,
        SetRole.WORKING,
    ]
    assert [planned_set.target_load for planned_set in exercise.planned_sets] == [None, None, None]
