from datetime import date

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.engine.band_composite import Band, ht_next_setup
from ironlog.models.enums import (
    FeedbackTap, GroupType, LiftCategory, Objective, Phase, ProgressionMode,
    ProgressionRule, Scheme, SetRole,
)
from ironlog.models.library import BandPair, EngineState, Movement, MovementState, PhasePolicy
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731
DAY_ROLE = "D2 Lower A"


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_common(db):
    db.add(EngineState(id=1, current_phase=Phase.CUT))
    db.add(PhasePolicy(
        phase=Phase.CUT,
        default_objective=Objective.MAINTAIN,
        rpe_band_low=6.0,
        rpe_band_high=8.0,
        hard_cap=80.0,
        top_set_rpe=8.0,
        progression_attempted=False,
        volume_posture="reduce",
    ))
    db.add(BandPair(id=1, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0))
    db.add(BandPair(id=2, label="#1 Red", bottom_lb=36.0, peak_lb=90.0))
    db.add(Movement(
        id=1,
        name="Hip Thrust [HIP_THRUST]",
        base_name="Hip Thrust",
        lift_category=LiftCategory.HIP_THRUST,
        progression_mode=ProgressionMode.COMPOSITE,
        progression_rule=ProgressionRule.RULE_DRIVEN.value,
        objective_override=Objective.PROGRESS,
    ))
    db.add(MovementState(
        movement_id=1,
        day_id=DAY_ROLE,
        ht_plates=205.0,
        ht_band_config=[1],
    ))
    db.commit()


def _seed_ht_session(db, *, session_id, feedback=FeedbackTap.ON_TARGET, reps=8):
    db.add(IronSession(
        id=session_id,
        date=date(2026, 7, 20),
        day_role=DAY_ROLE,
        phase=Phase.CUT,
    ))
    db.add(ExerciseGroup(
        id=session_id * 10,
        session_id=session_id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        label="T1",
    ))
    db.add(PlannedExercise(
        id=session_id * 10,
        group_id=session_id * 10,
        movement_id=1,
        order_index=0,
        scheme=Scheme.STRAIGHT,
        objective=Objective.PROGRESS,
    ))
    for i in range(3):
        planned_set_id = session_id * 100 + i
        db.add(PlannedSet(
            id=planned_set_id,
            planned_exercise_id=session_id * 10,
            set_index=i,
            set_role=SetRole.WORKING,
            target_reps_low=6,
            target_reps_high=8,
            target_rpe=8.0,
            target_plates=205.0,
            band_config=[1],
        ))
        db.add(SetLog(
            planned_set_id=planned_set_id,
            session_id=session_id,
            movement_id=1,
            set_index=i,
            actual_reps=reps,
            feedback_tap=feedback,
            actual_plates=205.0,
            is_warmup=False,
        ))
    db.commit()


def _state(db):
    return db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()


def test_run_analysis_stages_ht_advance_after_clean_session():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_ht_session(db, session_id=1)
        expected = ht_next_setup(205.0, [1], [
            Band(1, 18.0, 45.0, True),
            Band(2, 36.0, 90.0, True),
        ])

        result = run_analysis(1, db, WEEK_KEYER)

        delta = result.movement_deltas[0]
        assert delta.pending_ht_plates == expected[0]
        assert delta.pending_ht_band_config == list(expected[1])

        state = _state(db)
        assert state.ht_plates == 205.0
        assert state.ht_band_config == [1]
        assert state.pending_ht_plates == expected[0]
        assert state.pending_ht_band_config == list(expected[1])


def test_run_analysis_dirty_ht_session_does_not_stage_advance():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_ht_session(db, session_id=1, feedback=FeedbackTap.TOO_HARD)

        result = run_analysis(1, db, WEEK_KEYER)

        delta = result.movement_deltas[0]
        assert delta.pending_ht_plates is None
        assert delta.pending_ht_band_config is None

        state = _state(db)
        assert state.ht_plates == 205.0
        assert state.ht_band_config == [1]
        assert state.pending_ht_plates is None
        assert state.pending_ht_band_config is None

