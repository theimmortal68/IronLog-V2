"""tests/test_felt_peak_refine.py — Task 5: single-band felt-peak refinement.

refine_from_logged_ht(session_id, db) reads the logged HT SetLogs for a
session, resolves each set's band configuration via PlannedSet.band_config
(fallback PlannedSet.band_pair_id, then SetLog.band_pair_id), and — for sets
whose resolved config has EXACTLY ONE band and a non-null felt_peak — nudges
that BandPair.peak_lb toward the observed peak (felt_peak - actual_plates)
via a running EMA. Multi-band sets are skipped entirely (can't isolate an
individual band's contribution to a stacked reading). After N=3 consistent
single-band readings on the same band (across sessions), calibration_status
flips MODELED -> MEASURED.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

import pytest
from sqlmodel import SQLModel, Session as DBSession, create_engine, select

from ironlog.models.enums import (
    BandCalStatus, FeedbackTap, GroupType, Objective, Scheme, SetRole,
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


def _log_ht_session(db, *, movement_id, band_config, target_plates, actual_plates,
                    felt_peak, session_date):
    """Plant one COMPLETED-shape HT working set: PlannedSet(band_config) ->
    SetLog(actual_plates, felt_peak). Returns the session id."""
    sess = IronSession(date=session_date, day_role="D2 Lower A", phase="P1")
    db.add(sess)
    db.commit()
    db.refresh(sess)

    grp = ExerciseGroup(session_id=sess.id, order_index=0, group_type=GroupType.STRAIGHT)
    db.add(grp)
    db.commit()
    db.refresh(grp)

    pex = PlannedExercise(group_id=grp.id, movement_id=movement_id, order_index=0,
                          scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN)
    db.add(pex)
    db.commit()
    db.refresh(pex)

    pset = PlannedSet(planned_exercise_id=pex.id, set_index=0, set_role=SetRole.WORKING,
                      target_plates=target_plates, band_config=band_config)
    db.add(pset)
    db.commit()
    db.refresh(pset)

    db.add(SetLog(
        planned_set_id=pset.id, session_id=sess.id, movement_id=movement_id, set_index=0,
        actual_plates=actual_plates, felt_peak=felt_peak,
        feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
    ))
    db.commit()
    return sess.id


@pytest.fixture
def db_with_ht_log():
    """logged HT: config=[Blue], plates=100, felt_peak=255 -> observed band
    peak = 155 (vs modeled 150)."""
    engine = _make_engine()
    with DBSession(engine) as db:
        blue = BandPair(label="Blue", bottom_lb=20.0, peak_lb=150.0)
        db.add(blue)
        db.add(Movement(id=1, name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust"))
        db.commit()
        db.refresh(blue)

        session_id = _log_ht_session(
            db, movement_id=1, band_config=[blue.id],
            target_plates=100.0, actual_plates=100.0, felt_peak=255.0,
            session_date=date(2026, 7, 1),
        )
        yield db, session_id, blue.id


@pytest.fixture
def db_with_multiband_ht_log():
    """logged HT: config=[Blue, Red] -> can't isolate either band, must skip."""
    engine = _make_engine()
    with DBSession(engine) as db:
        blue = BandPair(label="Blue", bottom_lb=20.0, peak_lb=150.0)
        red = BandPair(label="Red", bottom_lb=36.0, peak_lb=180.0)
        db.add(blue)
        db.add(red)
        db.add(Movement(id=1, name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust"))
        db.commit()
        db.refresh(blue)
        db.refresh(red)

        session_id = _log_ht_session(
            db, movement_id=1, band_config=[blue.id, red.id],
            target_plates=100.0, actual_plates=100.0, felt_peak=400.0,
            session_date=date(2026, 7, 1),
        )
        yield db, session_id


def test_single_band_log_refines_band_peak(db_with_ht_log):
    db, session_id, blue_id = db_with_ht_log
    refine_from_logged_ht(session_id, db)
    blue = db.get(BandPair, blue_id)
    assert abs(blue.peak_lb - 155) < 10   # moved toward observed via running estimate
    assert blue.peak_lb != 150.0          # actually moved, not a no-op


def test_multi_band_log_leaves_bands_untouched(db_with_multiband_ht_log):
    db, session_id = db_with_multiband_ht_log
    before = {b.id: b.peak_lb for b in db.exec(select(BandPair)).all()}
    refine_from_logged_ht(session_id, db)
    after = {b.id: b.peak_lb for b in db.exec(select(BandPair)).all()}
    assert after == before   # can't isolate individual bands in a stack


def test_single_band_flips_to_measured_after_three_consistent_readings():
    engine = _make_engine()
    with DBSession(engine) as db:
        blue = BandPair(label="Blue", bottom_lb=20.0, peak_lb=150.0)
        db.add(blue)
        db.add(Movement(id=1, name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust"))
        db.commit()
        db.refresh(blue)
        assert blue.calibration_status == BandCalStatus.MODELED

        for i in range(3):
            sid = _log_ht_session(
                db, movement_id=1, band_config=[blue.id],
                target_plates=100.0, actual_plates=100.0, felt_peak=255.0,
                session_date=date(2026, 7, 1 + i),
            )
            refine_from_logged_ht(sid, db)

        blue = db.get(BandPair, blue.id)
        assert blue.calibration_status == BandCalStatus.MEASURED


def test_two_readings_do_not_yet_flip_to_measured():
    engine = _make_engine()
    with DBSession(engine) as db:
        blue = BandPair(label="Blue", bottom_lb=20.0, peak_lb=150.0)
        db.add(blue)
        db.add(Movement(id=1, name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust"))
        db.commit()
        db.refresh(blue)

        for i in range(2):
            sid = _log_ht_session(
                db, movement_id=1, band_config=[blue.id],
                target_plates=100.0, actual_plates=100.0, felt_peak=255.0,
                session_date=date(2026, 7, 1 + i),
            )
            refine_from_logged_ht(sid, db)

        blue = db.get(BandPair, blue.id)
        assert blue.calibration_status == BandCalStatus.MODELED
