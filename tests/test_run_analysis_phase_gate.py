"""Integration tests for run_analysis's phase gate wiring."""
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session as DbSession, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from ironlog.models.library import (
    DailyReadiness, E1rmHistory, EngineState, Movement, MovementState, PhasePolicy, GoalSettings
)
from ironlog.models.session import (
    Session as WorkoutSession, SessionStatus, SetLog, PlannedSet, ExerciseGroup, PlannedExercise
)
from ironlog.models.enums import Objective, Phase, FeedbackTap, GroupType, Scheme, SetRole
from ironlog.persistence.run_analysis import run_analysis

def _week_keyer(d):
    iso = d.isocalendar()
    return (iso[0], iso[1])

def _setup_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with DbSession(engine) as db:
        db.add(PhasePolicy(
            phase=Phase.STAB,
            default_objective=Objective.PROGRESS,
            rpe_band_low=6.0,
            rpe_band_high=8.0,
            hard_cap=80.0,
            top_set_rpe=8.0,
            progression_attempted=False,
            volume_posture="reduce",
        ))
        db.add(PhasePolicy(
            phase=Phase.REBUILD,
            default_objective=Objective.PROGRESS,
            rpe_band_low=6.0,
            rpe_band_high=8.0,
            hard_cap=80.0,
            top_set_rpe=8.0,
            progression_attempted=False,
            volume_posture="reduce",
        ))
        db.add(EngineState(id=1, current_phase=Phase.STAB))
        db.add(Movement(id=1, name="Squat", base_name="Squat", progression_rule="test"))
        db.add(MovementState(id=1, movement_id=1, day_id="dayA"))
        db.commit()
    return engine

def _seed_readiness(db, today: date):
    # Baseline RHR: 80 days ago
    for i in range(80, 85):
        d = today - timedelta(days=i)
        db.add(DailyReadiness(date=d, resting_hr=60.0))
        
    # Recent 14 days (bw stable, rhr down, sleep ok, subj ok)
    for i in range(1, 15):
        d = today - timedelta(days=i)
        rhr = 55.0 if i < 7 else 60.0
        db.add(DailyReadiness(
            date=d,
            bodyweight=200.0,      # stable
            resting_hr=rhr,       # down from 60
            sleep_ok=True,         # ok
            subjective_ok=True     # ok
        ))

def _seed_e1rm_history(db, today: date):
    # We need 3 PROGRESS rows for stall (RPE creep), and 4+ rows in last 6 weeks for strength bounce
    # Bounce: earlier half flat/declining, later half rising
    # Last 6 weeks = 42 days.
    
    # 1. date = today - 30 (e1rm=100) (flat/decline start)
    # 2. date = today - 25 (e1rm=99)  (flat/decline end)
    # 3. date = today - 15 (e1rm=99)  (rise start)
    # 4. date = today - 5  (e1rm=110) (rise end)
    
    # These must be PROGRESS to count for RPE creep as well.
    # We need 3+ for RPE creep.
    # Early RPE = 8, Late RPE = 8 (no creep)
    
    history = [
        (30, 100.0, 8.0),
        (25, 99.0, 8.0),
        (15, 99.0, 8.0),
        (5, 110.0, 8.0),
    ]
    for idx, (days_ago, e1rm, rpe) in enumerate(history, 1):
        d = today - timedelta(days=days_ago)
        db.add(WorkoutSession(id=100+idx, date=d, day_role="dayA", phase=Phase.STAB, status=SessionStatus.COMPLETED))
        db.add(E1rmHistory(
            movement_id=1,
            session_id=100+idx,
            e1rm=e1rm,
            objective=Objective.PROGRESS,
            phase=Phase.STAB,
            anchor_load=100,
            anchor_reps=5,
            anchor_rpe=rpe,
            computed_at=datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc)
        ))

def test_phase_gate_opens_when_conditions_met():
    engine = _setup_db()
    today = date.today()
    
    with DbSession(engine) as db:
        _seed_readiness(db, today)
        _seed_e1rm_history(db, today)
        
        # Current session (needs an anchor to be analyzed properly, though gate logic runs regardless)
        ws = WorkoutSession(id=1, date=today, day_role="dayA", phase=Phase.STAB, status=SessionStatus.COMPLETED)
        db.add(ws)
        
        eg = ExerciseGroup(id=1, session_id=1, order_index=0, group_type=GroupType.STRAIGHT, label="Main")
        db.add(eg)
        pe = PlannedExercise(id=1, group_id=1, movement_id=1, order_index=0, scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
        db.add(pe)
        
        ps = PlannedSet(id=1, planned_exercise_id=1, set_index=0, set_role=SetRole.WORKING, target_rpe=8.0, target_reps_low=5, target_reps_high=5)
        db.add(ps)
        sl = SetLog(session_id=1, movement_id=1, planned_set_id=1, set_index=0, actual_load=120, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False)
        db.add(sl)
        db.commit()

    with DbSession(engine) as db:
        result = run_analysis(1, db, _week_keyer)
    
    assert result.phase_transition_available == Phase.REBUILD

def test_phase_gate_stays_closed_insufficient_data():
    engine = _setup_db()
    today = date.today()
    
    with DbSession(engine) as db:
        # Don't seed readiness data!
        _seed_e1rm_history(db, today)
        
        ws = WorkoutSession(id=1, date=today, day_role="dayA", phase=Phase.STAB, status=SessionStatus.COMPLETED)
        db.add(ws)
        
        eg = ExerciseGroup(id=1, session_id=1, order_index=0, group_type=GroupType.STRAIGHT, label="Main")
        db.add(eg)
        pe = PlannedExercise(id=1, group_id=1, movement_id=1, order_index=0, scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
        db.add(pe)
        
        ps = PlannedSet(id=1, planned_exercise_id=1, set_index=0, set_role=SetRole.WORKING, target_rpe=8.0, target_reps_low=5, target_reps_high=5)
        db.add(ps)
        sl = SetLog(session_id=1, movement_id=1, planned_set_id=1, set_index=0, actual_load=120, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False)
        db.add(sl)
        db.commit()

    with DbSession(engine) as db:
        result = run_analysis(1, db, _week_keyer)
    
    assert result.phase_transition_available is None

def _setup_db_cut():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with DbSession(engine) as db:
        db.add(PhasePolicy(
            phase=Phase.CUT,
            default_objective=Objective.MAINTAIN,
            rpe_band_low=7.0,
            rpe_band_high=9.0,
            hard_cap=100.0,
            top_set_rpe=8.0,
            progression_attempted=False,
            volume_posture="reduce",
        ))
        db.add(EngineState(id=1, current_phase=Phase.CUT))
        db.add(Movement(id=1, name="Squat", base_name="Squat", progression_rule="test"))
        db.add(MovementState(id=1, movement_id=1, day_id="dayA"))
        db.commit()
    return engine

def test_cut_gate_no_goal_settings_fallback_met():
    engine = _setup_db_cut()
    today = date.today()
    with DbSession(engine) as db:
        for i in range(14):
            db.add(DailyReadiness(date=today - timedelta(days=i), bodyweight=214.0))
        db.add(WorkoutSession(id=1, date=today, day_role="dayA", phase=Phase.CUT, status=SessionStatus.COMPLETED))
        db.commit()

    with DbSession(engine) as db:
        result = run_analysis(1, db, _week_keyer)
    assert result.phase_transition_available == Phase.STAB

def test_cut_gate_configured_weight_goal_met():
    engine = _setup_db_cut()
    today = date.today()
    with DbSession(engine) as db:
        db.add(GoalSettings(id=1, target_bodyweight=200.0, target_bodyweight_tolerance=1.0))
        for i in range(14):
            db.add(DailyReadiness(date=today - timedelta(days=i), bodyweight=201.0))
        db.add(WorkoutSession(id=1, date=today, day_role="dayA", phase=Phase.CUT, status=SessionStatus.COMPLETED))
        db.commit()

    with DbSession(engine) as db:
        result = run_analysis(1, db, _week_keyer)
    assert result.phase_transition_available == Phase.STAB

def test_cut_gate_configured_body_fat_goal_met_alone():
    engine = _setup_db_cut()
    today = date.today()
    with DbSession(engine) as db:
        db.add(GoalSettings(
            id=1,
            target_bodyweight=200.0, target_bodyweight_tolerance=1.0,
            target_body_fat_pct=15.0, target_body_fat_pct_tolerance=0.5
        ))
        for i in range(14):
            db.add(DailyReadiness(date=today - timedelta(days=i), bodyweight=210.0, body_fat_pct=15.5))
        db.add(WorkoutSession(id=1, date=today, day_role="dayA", phase=Phase.CUT, status=SessionStatus.COMPLETED))
        db.commit()

    with DbSession(engine) as db:
        result = run_analysis(1, db, _week_keyer)
    assert result.phase_transition_available == Phase.STAB

def test_cut_gate_neither_clears():
    engine = _setup_db_cut()
    today = date.today()
    with DbSession(engine) as db:
        db.add(GoalSettings(
            id=1,
            target_bodyweight=200.0, target_bodyweight_tolerance=1.0,
            target_body_fat_pct=15.0, target_body_fat_pct_tolerance=0.5
        ))
        for i in range(14):
            db.add(DailyReadiness(date=today - timedelta(days=i), bodyweight=210.0, body_fat_pct=16.0))
        db.add(WorkoutSession(id=1, date=today, day_role="dayA", phase=Phase.CUT, status=SessionStatus.COMPLETED))
        db.commit()

    with DbSession(engine) as db:
        result = run_analysis(1, db, _week_keyer)
    assert result.phase_transition_available is None
