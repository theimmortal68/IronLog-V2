"""Tests for ironlog.persistence.apply.apply_analysis (v0.4).

The first DB-touching test file. Uses an in-memory SQLite session; the
engine tests (tests/test_analysis.py) stay pure.
"""
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.library import EngineState, MovementState
from ironlog.models.enums import Phase
from ironlog.engine.analysis import AnalysisResult, MovementStateDelta
from ironlog.persistence.apply import apply_analysis


@pytest.fixture
def db():
    engine = create_engine("sqlite://")  # in-memory
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
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
