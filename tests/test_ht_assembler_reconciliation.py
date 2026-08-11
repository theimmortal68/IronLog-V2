"""Regression tests for HT performed-floor reconciliation in the assembler.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from datetime import date

from sqlmodel import select

from ironlog.engine.band_composite import Band, ht_next_setup
from ironlog.generation.assembler import assemble
from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import BandPair, Movement, MovementState
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)

# 2026-08-11 (STAB maintenance-block redesign, Task 2): was "D2 Lower A" --
# D2's Hip Thrust T1b tier was removed entirely, so this generic HT
# reconciliation test now exercises D5's still-live Hip Thrust slot instead.
DAY_ROLE = "D5 Lower B"
WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def _ht_movement(db):
    return db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()


def _band(db, label):
    return db.exec(select(BandPair).where(BandPair.label == label)).one()


def _inventory(db):
    return [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
            for bp in db.exec(select(BandPair)).all()]


def _set_current_ht_setup(db, movement_id, plates, config):
    st = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            MovementState.day_id == DAY_ROLE,
        )
    ).one()
    st.ht_plates = plates
    st.ht_band_config = list(config)
    db.add(st)
    db.commit()


def _add_completed_ht_log(db, movement_id, band_config, actual_plates, felt_peak):
    session = IronSession(
        date=date(2026, 7, 21),
        day_role=DAY_ROLE,
        phase="CUT",
        status=SessionStatus.COMPLETED,
    )
    db.add(session)
    db.flush()

    group = ExerciseGroup(
        session_id=session.id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
    )
    db.add(group)
    db.flush()

    planned_exercise = PlannedExercise(
        group_id=group.id,
        movement_id=movement_id,
        order_index=0,
        scheme=Scheme.STRAIGHT,
        objective=Objective.MAINTAIN,
    )
    db.add(planned_exercise)
    db.flush()

    planned_set = PlannedSet(
        planned_exercise_id=planned_exercise.id,
        set_index=2,
        set_role=SetRole.WORKING,
        target_plates=actual_plates - 5,
        band_config=list(band_config),
    )
    db.add(planned_set)
    db.flush()

    db.add(SetLog(
        planned_set_id=planned_set.id,
        session_id=session.id,
        movement_id=movement_id,
        set_index=2,
        actual_plates=actual_plates,
        felt_peak=felt_peak,
        feedback_tap=FeedbackTap.ON_TARGET,
        is_warmup=False,
    ))
    db.commit()


def _assemble_d2(db):
    skeleton = lay_skeleton(DAY_ROLE, db)
    ctx = resolve_context(DAY_ROLE, skeleton, db, WEEK_KEYER)
    return assemble(program_selections(skeleton), skeleton, ctx, db)


def _ht_sets(assembled, movement_id):
    return [
        ps
        for group in assembled.session.groups
        for ex in group.exercises
        if ex.movement_id == movement_id
        for ps in ex.planned_sets
    ]


def test_same_config_felt_peak_floors_next_setup_from_performed_plates(gen_db):
    seed_movement_baselines(gen_db)
    hip_thrust = _ht_movement(gen_db)
    red = _band(gen_db, "#1 Red")
    _set_current_ht_setup(gen_db, hip_thrust.id, 165.0, [red.id])
    _add_completed_ht_log(
        gen_db,
        hip_thrust.id,
        [red.id],
        actual_plates=170.0,
        felt_peak=260.0,
    )

    inventory = _inventory(gen_db)
    stale_next = ht_next_setup(165.0, [red.id], inventory)
    reconciled_next = ht_next_setup(170.0, [red.id], inventory)
    assert stale_next != reconciled_next

    assembled = _assemble_d2(gen_db)

    assert assembled.prospective_ht_setups[hip_thrust.id] == (170.0, [red.id])
    ht_sets = _ht_sets(assembled, hip_thrust.id)
    assert ht_sets
    assert all(ps.target_plates == 170.0 for ps in ht_sets)
    assert all(ps.band_config == [red.id] for ps in ht_sets)


def test_different_logged_config_does_not_reconcile(gen_db):
    seed_movement_baselines(gen_db)
    hip_thrust = _ht_movement(gen_db)
    orange = _band(gen_db, "#0 Orange")
    red = _band(gen_db, "#1 Red")
    _set_current_ht_setup(gen_db, hip_thrust.id, 165.0, [red.id])
    _add_completed_ht_log(
        gen_db,
        hip_thrust.id,
        [orange.id],
        actual_plates=215.0,
        felt_peak=260.0,
    )

    assembled = _assemble_d2(gen_db)

    assert assembled.prospective_ht_setups[hip_thrust.id] == (165.0, [red.id])
    ht_sets = _ht_sets(assembled, hip_thrust.id)
    assert ht_sets
    assert all(ps.target_plates == 165.0 for ps in ht_sets)
    assert all(ps.band_config == [red.id] for ps in ht_sets)


def test_no_prior_completed_session_leaves_ht_setup_unchanged(gen_db):
    seed_movement_baselines(gen_db)
    hip_thrust = _ht_movement(gen_db)
    red = _band(gen_db, "#1 Red")
    _set_current_ht_setup(gen_db, hip_thrust.id, 165.0, [red.id])

    assembled = _assemble_d2(gen_db)

    assert assembled.prospective_ht_setups[hip_thrust.id] == (165.0, [red.id])
    ht_sets = _ht_sets(assembled, hip_thrust.id)
    assert ht_sets
    assert all(ps.target_plates == 165.0 for ps in ht_sets)
    assert all(ps.band_config == [red.id] for ps in ht_sets)
