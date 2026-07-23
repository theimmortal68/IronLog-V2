from datetime import date

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.enums import (
    CalibrationStatus,
    FeedbackTap,
    GroupType,
    Objective,
    Phase,
    ProgressionMode,
    ProgressionRule,
    Scheme,
    SessionStatus,
    SetRole,
)
from ironlog.models.library import EngineState, Movement, MovementState, PhasePolicy
from ironlog.models.session import (
    ExerciseGroup,
    PlannedExercise,
    PlannedSet,
    Session as IronSession,
    SetLog,
)
from ironlog.persistence.run_analysis import _clean_performed_assist_values, run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


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


def _seed_movement(db, movement_id, **kw):
    kw.setdefault("objective_override", Objective.PROGRESS)
    kw.setdefault("progression_mode", ProgressionMode.ASSISTED)
    db.add(Movement(
        id=movement_id,
        name=f"Movement {movement_id}",
        base_name=f"Movement {movement_id}",
        **kw,
    ))


def _seed_session(db, session_id, movement_id, *, sets, label="T1", day_role="D2 Lower A",
                  session_date=date(2026, 7, 22)):
    db.add(IronSession(
        id=session_id,
        date=session_date,
        day_role=day_role,
        phase="CUT",
        status=SessionStatus.COMPLETED,
    ))
    grp_id = session_id * 100
    pex_id = grp_id + 1
    db.add(ExerciseGroup(
        id=grp_id,
        session_id=session_id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        label=label,
    ))
    db.add(PlannedExercise(
        id=pex_id,
        group_id=grp_id,
        movement_id=movement_id,
        order_index=0,
        scheme=Scheme.STRAIGHT,
        objective=Objective.PROGRESS,
    ))
    for i, row in enumerate(sets):
        ps_id = grp_id + 10 + i
        set_index = row.get("set_index", i)
        target_reps_high = row.get("target_reps_high", 8)
        db.add(PlannedSet(
            id=ps_id,
            planned_exercise_id=pex_id,
            set_index=set_index,
            set_role=SetRole.WORKING,
            target_load=row.get("target_load"),
            target_reps_low=row.get("target_reps_low", 6),
            target_reps_high=target_reps_high,
            target_rpe=row.get("target_rpe", 8.0),
        ))
        db.add(SetLog(
            planned_set_id=ps_id,
            session_id=session_id,
            movement_id=movement_id,
            set_index=set_index,
            actual_load=row.get("actual_load"),
            actual_reps=row.get("actual_reps", target_reps_high),
            feedback_tap=row.get("feedback_tap", FeedbackTap.ON_TARGET),
            is_warmup=row.get("is_warmup", False),
            actual_unassisted_reps=row.get("actual_unassisted_reps"),
        ))
    db.commit()


def _planned_set(ps_id, *, target_load=15.0, target_reps_high=8):
    return PlannedSet(
        id=ps_id,
        planned_exercise_id=1,
        set_index=ps_id,
        set_role=SetRole.WORKING,
        target_load=target_load,
        target_reps_high=target_reps_high,
    )


def _set_log(ps_id, *, movement_id=1, set_index=0, actual_load=15.0,
             actual_reps=8, is_warmup=False):
    return SetLog(
        planned_set_id=ps_id,
        session_id=1,
        movement_id=movement_id,
        set_index=set_index,
        actual_load=actual_load,
        actual_reps=actual_reps,
        is_warmup=is_warmup,
    )


def test_clean_performed_assist_values_returns_only_clean_set_group_values():
    planned_sets = {
        1: _planned_set(1, target_load=15.0),
        2: _planned_set(2, target_load=10.0),
        3: _planned_set(3, target_load=15.0),
        4: _planned_set(4, target_load=5.0),
        5: _planned_set(5, target_load=25.0),
    }
    set_logs = [
        _set_log(1, set_index=0, actual_load=15.0, actual_reps=8),
        _set_log(2, set_index=1, actual_load=10.0, actual_reps=5),
        _set_log(3, set_index=2, actual_load=15.0, actual_reps=8),
        _set_log(4, set_index=3, actual_load=5.0, actual_reps=8, is_warmup=True),
        _set_log(5, movement_id=2, set_index=0, actual_load=25.0, actual_reps=8),
    ]

    assert _clean_performed_assist_values(1, set_logs, planned_sets) == [15.0, 15.0]


def test_clean_performed_assist_values_falls_back_to_planned_target_load():
    planned_sets = {1: _planned_set(1, target_load=15.0)}
    set_logs = [_set_log(1, actual_load=None, actual_reps=8)]

    assert _clean_performed_assist_values(1, set_logs, planned_sets) == [15.0]


def test_clean_performed_assist_values_returns_empty_when_no_clean_sets():
    planned_sets = {1: _planned_set(1, target_load=15.0)}
    set_logs = [_set_log(1, actual_load=15.0, actual_reps=7)]

    assert _clean_performed_assist_values(1, set_logs, planned_sets) == []
    assert _clean_performed_assist_values(2, set_logs, planned_sets) == []


def test_run_analysis_floors_to_clean_harder_rung_but_not_failed_probe():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(
            db,
            1,
            progression_rule=ProgressionRule.INCLINE_REDUCTION.value,
            assist_ladder=[20, 15, 10, 5, 0],
        )
        db.add(MovementState(
            movement_id=1,
            calibration_status=CalibrationStatus.MEASURED,
            assist_level=20.0,
            consecutive_advance_count=1,
        ))
        db.commit()
        _seed_session(db, 1, 1, sets=[
            {"target_load": 20.0, "actual_load": 15.0, "actual_reps": 8},
            {
                "target_load": 20.0,
                "actual_load": 10.0,
                "actual_reps": 5,
                "feedback_tap": FeedbackTap.TOO_HARD,
            },
            {"target_load": 20.0, "actual_load": 15.0, "actual_reps": 8},
        ])

        result = run_analysis(1, db, WEEK_KEYER)

        delta = result.movement_deltas[0]
        assert delta.new_assist_level == 15.0
        assert delta.new_consecutive_advance_count == 0

        state = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert state.assist_level == 15.0
        assert state.consecutive_advance_count == 0
        assert state.active_rule == ProgressionRule.INCLINE_REDUCTION.value


def test_run_analysis_pull_up_rolling_max_does_not_enter_assist_floor_branch(monkeypatch):
    calls = []

    def _record_call(current, ladder, clean_values):
        calls.append((current, ladder, clean_values))
        return current

    monkeypatch.setattr("ironlog.persistence.run_analysis.performed_assist_floor", _record_call)

    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(
            db,
            2,
            progression_rule=ProgressionRule.PULL_UP_ROLLING_MAX.value,
            assist_ladder=None,
        )
        db.add(MovementState(
            movement_id=2,
            calibration_status=CalibrationStatus.MEASURED,
            assist_level=42.0,
            unassisted_max_rolling=6,
        ))
        db.commit()
        _seed_session(db, 2, 2, sets=[
            {
                "actual_load": 10.0,
                "actual_reps": 8,
                "actual_unassisted_reps": 9,
            },
        ])

        result = run_analysis(2, db, WEEK_KEYER)

        delta = result.movement_deltas[0]
        assert calls == []
        assert delta.new_assist_level is None
        assert delta.new_unassisted_max_rolling == 9

        state = db.exec(select(MovementState).where(MovementState.movement_id == 2)).one()
        assert state.assist_level == 42.0
        assert state.unassisted_max_rolling == 9
        assert state.active_rule == ProgressionRule.PULL_UP_ROLLING_MAX.value
