"""Regression tests for HT band peak refinement.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

from sqlmodel import SQLModel, Session as DBSession, create_engine

from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, Scheme, SetRole,
)
from ironlog.models.library import BandPair, Movement
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet,
    Session as IronSession, SetLog,
)
from ironlog.persistence.ht_refine import refine_from_logged_ht


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _add_movement(db):
    movement = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
    db.add(movement)
    db.flush()
    return movement.id


def _add_ht_session(db, *, movement_id, logs):
    session = IronSession(date=date(2026, 7, 17), day_role="D2 Lower A", phase="P1")
    db.add(session)
    db.flush()

    group = ExerciseGroup(
        session_id=session.id, order_index=0, group_type=GroupType.STRAIGHT,
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

    for index, log in enumerate(logs):
        planned_set = PlannedSet(
            planned_exercise_id=planned_exercise.id,
            set_index=index,
            set_role=SetRole.WORKING,
            target_plates=log["plates"],
            band_config=log["band_config"],
        )
        db.add(planned_set)
        db.flush()

        db.add(SetLog(
            planned_set_id=planned_set.id,
            session_id=session.id,
            movement_id=movement_id,
            set_index=index,
            actual_plates=log["plates"],
            felt_peak=log["felt_peak"],
            feedback_tap=FeedbackTap.ON_TARGET,
            is_warmup=False,
        ))

    db.commit()
    return session.id


def _ema(start_peak, observed):
    return round(0.7 * start_peak + 0.3 * observed, 1)


def test_three_identical_single_band_logs_apply_one_session_ema():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=1, label="Red", bottom_lb=36.0, peak_lb=90.0))
        movement_id = _add_movement(db)
        db.commit()

        session_id = _add_ht_session(
            db,
            movement_id=movement_id,
            logs=[
                {"band_config": [1], "plates": 165.0, "felt_peak": 260.0},
                {"band_config": [1], "plates": 165.0, "felt_peak": 260.0},
                {"band_config": [1], "plates": 165.0, "felt_peak": 260.0},
            ],
        )

        refine_from_logged_ht(session_id, db)

        assert db.get(BandPair, 1).peak_lb == 91.5


def test_single_qualifying_log_matches_one_shot_ema():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=1, label="Red", bottom_lb=36.0, peak_lb=90.0))
        movement_id = _add_movement(db)
        db.commit()

        session_id = _add_ht_session(
            db,
            movement_id=movement_id,
            logs=[
                {"band_config": [1], "plates": 165.0, "felt_peak": 260.0},
            ],
        )

        refine_from_logged_ht(session_id, db)

        assert db.get(BandPair, 1).peak_lb == _ema(90.0, 95.0)


def test_multiple_logs_for_same_band_use_mean_observed_peak():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=1, label="Red", bottom_lb=36.0, peak_lb=90.0))
        movement_id = _add_movement(db)
        db.commit()

        session_id = _add_ht_session(
            db,
            movement_id=movement_id,
            logs=[
                {"band_config": [1], "plates": 165.0, "felt_peak": 260.0},
                {"band_config": [1], "plates": 165.0, "felt_peak": 270.0},
            ],
        )

        refine_from_logged_ht(session_id, db)

        assert db.get(BandPair, 1).peak_lb == _ema(90.0, 100.0)


def test_two_single_band_configs_update_independently():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=1, label="Orange", bottom_lb=18.0, peak_lb=45.0))
        db.add(BandPair(id=2, label="Red", bottom_lb=36.0, peak_lb=90.0))
        movement_id = _add_movement(db)
        db.commit()

        session_id = _add_ht_session(
            db,
            movement_id=movement_id,
            logs=[
                {"band_config": [1], "plates": 180.0, "felt_peak": 225.0},
                {"band_config": [1], "plates": 180.0, "felt_peak": 235.0},
                {"band_config": [2], "plates": 165.0, "felt_peak": 260.0},
                {"band_config": [2], "plates": 165.0, "felt_peak": 270.0},
            ],
        )

        refine_from_logged_ht(session_id, db)

        assert db.get(BandPair, 1).peak_lb == _ema(45.0, 50.0)
        assert db.get(BandPair, 2).peak_lb == _ema(90.0, 100.0)


def test_multi_band_config_is_skipped_entirely():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=1, label="Orange", bottom_lb=18.0, peak_lb=45.0))
        db.add(BandPair(id=2, label="Red", bottom_lb=36.0, peak_lb=90.0))
        movement_id = _add_movement(db)
        db.commit()

        session_id = _add_ht_session(
            db,
            movement_id=movement_id,
            logs=[
                {"band_config": [1, 2], "plates": 165.0, "felt_peak": 300.0},
            ],
        )

        refine_from_logged_ht(session_id, db)

        assert db.get(BandPair, 1).peak_lb == 45.0
        assert db.get(BandPair, 2).peak_lb == 90.0
