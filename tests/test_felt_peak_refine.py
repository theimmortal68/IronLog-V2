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


def test_three_sets_one_session_does_not_flip():
    engine = _make_engine()
    with DBSession(engine) as db:
        band = BandPair(id=0, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0,
                        calibration_status=BandCalStatus.MODELED)
        mv = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
        db.add(band); db.add(mv); db.commit(); db.refresh(band); db.refresh(mv)
        # ONE session; three qualifying single-band sets on that session.
        sid = _log_ht_session(db, movement_id=mv.id, band_config=[0],
                              target_plates=180.0, actual_plates=180.0,
                              felt_peak=225.0, session_date=date(2026, 7, 1))
        # add two more sets to the SAME session
        grp = ExerciseGroup(session_id=sid, order_index=1, group_type=GroupType.STRAIGHT)
        db.add(grp); db.commit(); db.refresh(grp)
        pex = PlannedExercise(group_id=grp.id, movement_id=mv.id, order_index=0,
                              scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN)
        db.add(pex); db.commit(); db.refresh(pex)
        for _ in range(2):
            ps = PlannedSet(planned_exercise_id=pex.id, set_index=0, set_role=SetRole.WORKING,
                            target_plates=180.0, band_config=[0])
            db.add(ps); db.commit(); db.refresh(ps)
            db.add(SetLog(session_id=sid, movement_id=mv.id, planned_set_id=ps.id,
                          set_index=0, set_role=SetRole.WORKING, is_warmup=False,
                          actual_plates=180.0, felt_peak=225.0,
                          feedback_tap=FeedbackTap.ON_TARGET))
        db.commit()

        refine_from_logged_ht(sid, db)
        assert db.get(BandPair, 0).calibration_status == BandCalStatus.MODELED


def _plant_and_refine_sessions(db, mv_id, peaks):
    """One qualifying single-band (Orange) session per felt_peak in `peaks`,
    plates 180, then refine each. Returns the final band status."""
    for i, fp in enumerate(peaks):
        sid = _log_ht_session(db, movement_id=mv_id, band_config=[0],
                              target_plates=180.0, actual_plates=180.0,
                              felt_peak=fp, session_date=date(2026, 7, 1 + i))
        refine_from_logged_ht(sid, db)
    return db.get(BandPair, 0).calibration_status


def test_three_consistent_sessions_flip_to_measured():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=0, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0,
                        calibration_status=BandCalStatus.MODELED))
        mv = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
        db.add(mv); db.commit(); db.refresh(mv)
        # observed = felt_peak-180 -> 45,46,47 : spread 2, mean 46 -> ~4% <= 15%
        status = _plant_and_refine_sessions(db, mv.id, [225.0, 226.0, 227.0])
        assert status == BandCalStatus.MEASURED


def test_three_sessions_with_outlier_stay_modeled():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=0, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0,
                        calibration_status=BandCalStatus.MODELED))
        mv = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
        db.add(mv); db.commit(); db.refresh(mv)
        # observed 45, 46, 70 -> spread 25 over mean ~53.7 = 47% > 15% -> not consistent
        status = _plant_and_refine_sessions(db, mv.id, [225.0, 226.0, 250.0])
        assert status == BandCalStatus.MODELED
