"""Tests for ironlog.persistence.apply.apply_analysis (v0.4 + v0.5).

The first DB-touching test file. Uses an in-memory SQLite session; the
engine tests (tests/test_analysis.py) stay pure.
"""
import pytest
from datetime import date, datetime
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.library import EngineState, Movement, MovementState
from ironlog.models.session import Session as IronSession
from ironlog.models.enums import CalibrationStatus, Phase
from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
from ironlog.persistence.apply import apply_analysis


@pytest.fixture
def db():
    engine = create_engine("sqlite://")  # in-memory
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def db_with_state():
    """In-memory DB seeded with Movement(id=1), MovementState(movement_id=1), Session(id=1)."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        mv = Movement(id=1, name="Back Squat [PB]", base_name="Back Squat")
        session.add(mv)
        state = MovementState(
            movement_id=1,
            current_load=100.0,
            calibration_status=CalibrationStatus.CALIBRATING,
        )
        session.add(state)
        sess = IronSession(
            id=1,
            date=date(2026, 6, 26),
            day_role="Upper A",
            phase="CUT",
        )
        session.add(sess)
        session.commit()
        yield session


def _seed_state(db, movement_id, **kw):
    state = MovementState(movement_id=movement_id, **kw)
    db.add(state)
    db.commit()
    return state


def test_each_field_written_when_delta_present(db):
    _seed_state(db, 1, e1rm=200.0, current_increment_tier=0,
                consecutive_ceiling_sessions=1, consecutive_failed_progressions=1)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=210.0, new_tier=1,
                           new_consecutive_ceiling=2, new_consecutive_failed=0),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 210.0
    assert s.current_increment_tier == 1
    assert s.consecutive_ceiling_sessions == 2
    assert s.consecutive_failed_progressions == 0


def test_none_fields_leave_columns_untouched(db):
    _seed_state(db, 1, e1rm=200.0, current_increment_tier=2,
                consecutive_ceiling_sessions=3, consecutive_failed_progressions=4)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=None, new_tier=None,
                           new_consecutive_ceiling=None, new_consecutive_failed=None),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 200.0                          # untouched
    assert s.current_increment_tier == 2
    assert s.consecutive_ceiling_sessions == 3
    assert s.consecutive_failed_progressions == 4


def test_e1rm_write_sets_updated_at(db):
    _seed_state(db, 1, e1rm=None, e1rm_updated_at=None)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=180.0),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 180.0
    assert s.e1rm_updated_at is not None             # timestamp written alongside e1rm


def test_existing_e1rm_preserved_when_delta_e1rm_is_none(db):
    _seed_state(db, 1, e1rm=275.0)
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=None, new_consecutive_ceiling=1),
    ])
    apply_analysis(result, db)
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 275.0                           # not nulled
    assert s.consecutive_ceiling_sessions == 1


def test_phase_transition_available_does_not_write_current_phase(db):
    es = EngineState(id=1, current_phase=Phase.STAB)
    db.add(es); db.commit()
    _seed_state(db, 1, e1rm=200.0)
    result = AnalysisResult(
        movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=210.0)],
        phase_transition_available=Phase.REBUILD,
    )
    apply_analysis(result, db)
    es_after = db.exec(select(EngineState).where(EngineState.id == 1)).one()
    assert es_after.current_phase == Phase.STAB      # report-only: NOT flipped to REBUILD


def test_atomicity_missing_row_writes_nothing(db):
    _seed_state(db, 1, e1rm=200.0, consecutive_ceiling_sessions=0)
    # movement 2 has no MovementState row → .one() raises during resolve, pre-mutation.
    result = AnalysisResult(movement_deltas=[
        MovementStateDelta(movement_id=1, new_e1rm=999.0, new_consecutive_ceiling=9),
        MovementStateDelta(movement_id=2, new_e1rm=500.0),
    ])
    with pytest.raises(Exception):
        apply_analysis(result, db)
    db.rollback()  # clear the failed transaction before re-querying
    s = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert s.e1rm == 200.0                           # movement 1 unchanged — no partial write
    assert s.consecutive_ceiling_sessions == 0


# ---------------------------------------------------------------------------
# v0.5 tests — history append + calibration flip (require db_with_state)
# ---------------------------------------------------------------------------

def test_apply_appends_e1rm_history_row_when_session_and_phase_given(db_with_state):
    # db_with_state: a fixture with a movement(id=1), its movementstate, and a session(id=1)
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import Objective, Phase
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    delta = MovementStateDelta(
        movement_id=1, new_e1rm=205.0, objective=Objective.PROGRESS,
        anchor_load=180.0, anchor_reps=5, anchor_rpe=8.0,
    )
    apply_analysis(AnalysisResult(movement_deltas=[delta]), db_with_state,
                   session_id=1, phase=Phase.CUT)
    rows = db_with_state.exec(select(E1rmHistory)).all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.movement_id, r.session_id, r.e1rm) == (1, 1, 205.0)
    assert r.objective == Objective.PROGRESS and r.phase == Phase.CUT
    assert (r.anchor_load, r.anchor_reps, r.anchor_rpe) == (180.0, 5, 8.0)


def test_apply_no_history_row_when_no_anchor(db_with_state):
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import Phase
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    # no anchor -> new_e1rm None -> no history row
    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1)]),
                   db_with_state, session_id=1, phase=Phase.CUT)
    assert db_with_state.exec(select(E1rmHistory)).all() == []


def test_apply_writes_calibration_flip(db_with_state):
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import CalibrationStatus, Phase
    from ironlog.models.library import MovementState
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=205.0)]),
                   db_with_state, session_id=1, phase=Phase.CUT, calibration_flips=frozenset({1}))
    st = db_with_state.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
    assert st.calibration_status == CalibrationStatus.MEASURED


def test_apply_never_writes_current_load(db_with_state):
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.enums import Phase
    from ironlog.models.library import MovementState
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    before = db_with_state.exec(select(MovementState).where(MovementState.movement_id == 1)).one().current_load
    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=205.0)]),
                   db_with_state, session_id=1, phase=Phase.CUT, calibration_flips=frozenset({1}))
    after = db_with_state.exec(select(MovementState).where(MovementState.movement_id == 1)).one().current_load
    assert after == before  # current_load is untouched by the applier (two-writer boundary)


def test_apply_backward_compatible_without_new_kwargs(db_with_state):
    # existing call shape still works: no history append, no flip
    from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
    from ironlog.models.library import E1rmHistory
    from ironlog.persistence.apply import apply_analysis
    from sqlmodel import select

    apply_analysis(AnalysisResult(movement_deltas=[MovementStateDelta(movement_id=1, new_e1rm=205.0)]),
                   db_with_state)
    assert db_with_state.exec(select(E1rmHistory)).all() == []  # no session/phase -> no rows
