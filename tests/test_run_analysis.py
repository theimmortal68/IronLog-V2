"""Tests for persistence.run_analysis — the analyze->apply seam."""
from datetime import date, datetime, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.engine.stall import STALL_WINDOW
from ironlog.models.enums import (
    CalibrationStatus, FeedbackTap, GroupType, Objective, Phase, Scheme, SetRole,
)
from ironlog.models.library import E1rmHistory, EngineState, Movement, MovementState
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet,
    Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis, select_progress_window

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def seeded_db():
    """Movement(1) CALIBRATING PROGRESS, Session(1) with one tapped working set.

    actual_load=190, actual_reps=1, ON_TARGET, target_rpe=9.5:
        implied_rir = 10 - 9.5 + 0 = 0.5
        e1rm = 190 * (1 + 1.5/30) = 190 * 1.05 = 199.5
    Only one prior week → no flip (need ≥2 weekly estimates).
    """
    engine = _make_engine()
    with Session(engine) as db:
        db.add(EngineState(id=1, current_phase=Phase.CUT))
        db.add(Movement(
            id=1, name="Back Squat [PB]", base_name="Back Squat",
            objective_override=Objective.PROGRESS,
            increment_ladder=[2.5, 5.0],
        ))
        db.add(MovementState(
            movement_id=1,
            calibration_status=CalibrationStatus.CALIBRATING,
            current_load=100.0,
        ))
        # Session in week 2 of 2026 (Jan 7 = Wednesday)
        db.add(IronSession(id=1, date=date(2026, 1, 7), day_role="Upper A", phase="CUT"))
        db.add(ExerciseGroup(
            id=1, session_id=1, order_index=0, group_type=GroupType.STRAIGHT,
        ))
        db.add(PlannedExercise(
            id=1, group_id=1, movement_id=1, order_index=0,
            scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS,
        ))
        db.add(PlannedSet(
            id=1, planned_exercise_id=1, set_index=0, set_role=SetRole.WORKING,
            target_rpe=9.5, target_reps_low=1, target_reps_high=3,
        ))
        db.add(SetLog(
            planned_set_id=1, session_id=1, movement_id=1, set_index=0,
            actual_load=190.0, actual_reps=1,
            feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
        ))
        db.commit()
        yield db


@pytest.fixture
def seeded_db_two_weeks():
    """CALIBRATING lift with two prior history rows in week A (e1rm 200 and 150;
    max=200, mean=175) and current session in week B producing e1rm=199.5.

    Weekly maxes: [200.0, 199.5] → |200-199.5|/200 = 0.25% < 5% → FLIP.
    Weekly means: [175.0, 199.5] → |175-199.5|/199.5 = 12.3% > 5% → no flip.
    Proves max (not mean) drives calibration-flip bucketing.
    """
    engine = _make_engine()
    with Session(engine) as db:
        db.add(EngineState(id=1, current_phase=Phase.CUT))
        db.add(Movement(
            id=1, name="Back Squat [PB]", base_name="Back Squat",
            objective_override=Objective.PROGRESS,
            increment_ladder=[2.5],
        ))
        db.add(MovementState(
            movement_id=1,
            calibration_status=CalibrationStatus.CALIBRATING,
            current_load=190.0,
        ))
        # Prior sessions in week 1 of 2026 (Jan 1 = Thursday, Jan 2 = Friday)
        db.add(IronSession(id=10, date=date(2026, 1, 1), day_role="Upper A", phase="CUT"))
        db.add(IronSession(id=11, date=date(2026, 1, 2), day_role="Upper A", phase="CUT"))
        # Week A: two prior history rows — max=200, mean=175 (200+150)/2
        db.add(E1rmHistory(
            movement_id=1, session_id=10, e1rm=200.0, objective=Objective.PROGRESS,
            phase=Phase.CUT, anchor_load=190.0, anchor_reps=1, anchor_rpe=8.5,
            computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
        db.add(E1rmHistory(
            movement_id=1, session_id=11, e1rm=150.0, objective=Objective.PROGRESS,
            phase=Phase.CUT, anchor_load=143.0, anchor_reps=1, anchor_rpe=8.5,
            computed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ))
        # Current session in week 2 of 2026 (Jan 8 = Thursday)
        db.add(IronSession(id=2, date=date(2026, 1, 8), day_role="Upper A", phase="CUT"))
        db.add(ExerciseGroup(
            id=1, session_id=2, order_index=0, group_type=GroupType.STRAIGHT,
        ))
        db.add(PlannedExercise(
            id=1, group_id=1, movement_id=1, order_index=0,
            scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS,
        ))
        db.add(PlannedSet(
            id=1, planned_exercise_id=1, set_index=0, set_role=SetRole.WORKING,
            target_rpe=9.5, target_reps_low=1, target_reps_high=3,
        ))
        # actual_load=190, actual_reps=1, ON_TARGET, target_rpe=9.5 → e1rm=199.5
        db.add(SetLog(
            planned_set_id=1, session_id=2, movement_id=1, set_index=0,
            actual_load=190.0, actual_reps=1,
            feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
        ))
        db.commit()
        yield db


def test_appends_history_row_for_analyzed_session(seeded_db):
    run_analysis(1, seeded_db, WEEK_KEYER)
    rows = seeded_db.exec(
        select(E1rmHistory).where(E1rmHistory.session_id == 1)
    ).all()
    assert len(rows) == 1
    assert rows[0].objective == Objective.PROGRESS  # stamped from movement.objective_override


def test_calibration_flip_via_week_keyer_max_aggregation(seeded_db_two_weeks):
    # Week A has rows [200, 150]; mean=175, max=200.
    # Week B produces ~199.5. |200-199.5|/200=0.25%<5% -> flip with max.
    # With mean: |175-199.5|/199.5=12.3%>5% -> no flip. Proves max is used.
    run_analysis(2, seeded_db_two_weeks, WEEK_KEYER)
    st = seeded_db_two_weeks.exec(
        select(MovementState).where(MovementState.movement_id == 1)
    ).one()
    assert st.calibration_status == CalibrationStatus.MEASURED


def test_current_load_untouched(seeded_db):
    before = seeded_db.exec(
        select(MovementState).where(MovementState.movement_id == 1)
    ).one().current_load
    run_analysis(1, seeded_db, WEEK_KEYER)
    after = seeded_db.exec(
        select(MovementState).where(MovementState.movement_id == 1)
    ).one().current_load
    assert after == before  # current_load is never written by run_analysis or apply_analysis


def test_select_progress_window_excludes_maintenance():
    """Pure unit test — no DB. Verifies the PROGRESS-window helper excludes
    non-PROGRESS rows and returns the last STALL_WINDOW entries, oldest-first."""
    def row(e, obj, t):
        return E1rmHistory(
            movement_id=1, session_id=t, e1rm=e, objective=obj,
            phase=Phase.CUT, anchor_load=e * 0.9, anchor_reps=5,
            anchor_rpe=8.0, computed_at=datetime(2026, 1, t, tzinfo=timezone.utc),
        )

    rows = [
        row(100.0, Objective.PROGRESS, 1),
        row(999.0, Objective.MAINTAIN, 2),
        row(102.0, Objective.PROGRESS, 3),
        row(104.0, Objective.PROGRESS, 4),
    ]
    # last STALL_WINDOW PROGRESS rows, oldest-first; the MAINTAIN 999 is excluded
    assert select_progress_window(rows, STALL_WINDOW) == [100.0, 102.0, 104.0]
