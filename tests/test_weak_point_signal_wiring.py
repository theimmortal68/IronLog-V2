"""Tests for MovementWeaknessSignal wiring in run_analysis.py."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.enums import GroupType, Objective, Phase, Scheme, SetRole, FeedbackTap
from ironlog.models.library import E1rmHistory, EngineState, Movement, MovementState, PhasePolicy, MovementWeaknessSignal, GoalSettings
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])

def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine

def _seed_common(db):
    db.add(EngineState(id=1, current_phase="CUT", last_phase_change=date(2025, 1, 1)))
    db.add(PhasePolicy(
        phase="CUT",
        default_objective=Objective.PROGRESS,
        rpe_band_low=6.0,
        rpe_band_high=8.0,
        hard_cap=80.0,
        top_set_rpe=8.0,
        progression_attempted=False,
        volume_posture="reduce",
    ))
    db.add(GoalSettings(id=1, target_bodyweight=200.0, target_bodyweight_tolerance=2.0))
    db.commit()

def _seed_movement(db, movement_id, **kw):
    kw.setdefault("objective_override", Objective.PROGRESS)
    kw.setdefault("increment_ladder", [2.5, 5.0])
    db.add(Movement(id=movement_id, name=f"Movement {movement_id}",
                     base_name=f"Movement {movement_id}", **kw))
    db.add(MovementState(movement_id=movement_id))
    db.commit()

def _seed_history(db, movement_id, e1rms):
    """Seed STALL_WINDOW history rows."""
    base_date = datetime.utcnow() - timedelta(days=30)
    for i, val in enumerate(e1rms):
        db.add(E1rmHistory(
            movement_id=movement_id,
            session_id=999,
            e1rm=val,
            objective=Objective.PROGRESS,
            phase="CUT",
            anchor_load=100.0,
            anchor_reps=5,
            anchor_rpe=8.0,
            computed_at=base_date + timedelta(days=i),
        ))
    db.commit()

def _seed_session_with_movements(db, session_id, movement_ids):
    db.add(IronSession(id=session_id, date=date(2026, 1, 7), day_role="Upper A", phase="CUT"))
    for i, mid in enumerate(movement_ids):
        grp_id = session_id * 100 + i
        db.add(ExerciseGroup(
            id=grp_id, session_id=session_id, order_index=i,
            group_type=GroupType.STRAIGHT, label="T1",
        ))
        db.add(PlannedExercise(
            id=grp_id, group_id=grp_id, movement_id=mid, order_index=0,
            scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS,
        ))
        db.add(PlannedSet(
            id=grp_id, planned_exercise_id=grp_id, set_index=0, set_role=SetRole.WORKING,
            target_rpe=8.0, target_reps_low=5, target_reps_high=8,
        ))
        db.add(SetLog(
            planned_set_id=grp_id, session_id=session_id, movement_id=mid, set_index=0,
            actual_load=100.0, actual_reps=5,
            feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
        ))
    db.commit()

def test_weak_point_signal_stalled_not_lagging():
    """All movements have 0% growth. They are all stalled, but none are lagging (median is 0.0)."""
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        for i in range(1, 5):
            _seed_movement(db, i)
            _seed_history(db, i, [100.0, 100.0, 100.0])  # rate = 0.0
        
        _seed_session_with_movements(db, 1, [1, 2, 3, 4])
        run_analysis(1, db, WEEK_KEYER)
        
        signals = db.exec(select(MovementWeaknessSignal).where(MovementWeaknessSignal.session_id == 1)).all()
        assert len(signals) == 4
        for s in signals:
            assert s.growth_rate == 0.0
            assert s.stalled is True
            assert s.lagging is False
            assert s.is_weak is True

def test_weak_point_signal_lagging_not_stalled():
    """One movement grows 6%, others grow 15%.
    The 6% movement is NOT stalled (>1%), but IS lagging (0.15 - 0.06 = 0.09 >= 0.05).
    """
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1)
        _seed_history(db, 1, [100.0, 103.0, 106.0]) # rate = 0.06
        
        for i in range(2, 6):
            _seed_movement(db, i)
            _seed_history(db, i, [100.0, 107.0, 115.0]) # rate = 0.15
            
        _seed_session_with_movements(db, 2, [1, 2, 3, 4, 5])
        run_analysis(2, db, WEEK_KEYER)
        
        signals = {s.movement_id: s for s in db.exec(select(MovementWeaknessSignal).where(MovementWeaknessSignal.session_id == 2)).all()}
        assert len(signals) == 5
        
        s1 = signals[1]
        assert abs(s1.growth_rate - 0.06) < 1e-5
        assert s1.stalled is False
        assert s1.lagging is True
        assert s1.is_weak is True
        
        for i in range(2, 6):
            sx = signals[i]
            assert abs(sx.growth_rate - 0.15) < 1e-5
            assert sx.stalled is False
            assert sx.lagging is False
            assert sx.is_weak is False

def test_weak_point_signal_fallback_less_than_3():
    """Only 2 movements in session. Comparison population < 3. lagging should be False for all."""
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1)
        _seed_history(db, 1, [100.0, 90.0, 80.0]) # rate = -0.2 (stalled)
        
        _seed_movement(db, 2)
        _seed_history(db, 2, [100.0, 110.0, 120.0]) # rate = 0.2 (not stalled)
        
        _seed_session_with_movements(db, 3, [1, 2])
        run_analysis(3, db, WEEK_KEYER)
        
        signals = {s.movement_id: s for s in db.exec(select(MovementWeaknessSignal).where(MovementWeaknessSignal.session_id == 3)).all()}
        assert len(signals) == 2
        
        s1 = signals[1]
        assert s1.stalled is True
        assert s1.lagging is False
        assert s1.is_weak is True
        
        s2 = signals[2]
        assert s2.stalled is False
        assert s2.lagging is False
        assert s2.is_weak is False
