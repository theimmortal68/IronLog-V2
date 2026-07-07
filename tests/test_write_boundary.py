"""Write-boundary guardrail (Fork 7c / Option-C) for the progression engine.

`run_analysis` must NEVER write `MovementState.current_load` — that column is
owned exclusively by `commit_session` at approval time (docs/superpowers/specs/
2026-07-03-progression-engine-design.md §6, "Write boundary — Option C").

This seeds a clean single-session RPE-8-standard advance (T1 -> confirmation_
window=1) so the second assertion (earned state actually advanced) is
meaningful: the engine DID earn an advance, and STILL never touched
current_load.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.enums import (
    CalibrationStatus, FeedbackTap, GroupType, Objective, Phase, ProgressionRule,
    Scheme, SetRole,
)
from ironlog.models.library import EngineState, Movement, MovementState, PhasePolicy
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def seeded_clean_session():
    """Movement(1) RPE_8_STANDARD / T1, Session(1) with one working set that
    hits target_reps_high at RPE <= 8 (ON_TARGET @ target_rpe=8.0, reps=8)."""
    engine = _make_engine()
    with Session(engine) as db:
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
        db.add(Movement(
            id=1, name="Bench Press", base_name="Bench Press",
            objective_override=Objective.PROGRESS,
            increment_ladder=[2.5, 5.0],
            progression_rule=ProgressionRule.RPE_8_STANDARD.value,
        ))
        db.add(MovementState(
            movement_id=1,
            calibration_status=CalibrationStatus.MEASURED,
            current_load=135.0,
        ))
        db.add(IronSession(id=1, date=date(2026, 1, 7), day_role="Upper A", phase="CUT"))
        db.add(ExerciseGroup(
            id=1, session_id=1, order_index=0, group_type=GroupType.STRAIGHT, label="T1",
        ))
        db.add(PlannedExercise(
            id=1, group_id=1, movement_id=1, order_index=0,
            scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS,
        ))
        db.add(PlannedSet(
            id=1, planned_exercise_id=1, set_index=0, set_role=SetRole.WORKING,
            target_rpe=8.0, target_reps_low=5, target_reps_high=8,
        ))
        db.add(SetLog(
            planned_set_id=1, session_id=1, movement_id=1, set_index=0,
            actual_load=135.0, actual_reps=8,
            feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
        ))
        db.commit()
        yield db


def test_run_analysis_never_writes_current_load(seeded_clean_session):
    db = seeded_clean_session
    before = {ms.id: ms.current_load for ms in db.exec(select(MovementState)).all()}
    run_analysis(1, db, WEEK_KEYER)
    after = {ms.id: ms.current_load for ms in db.exec(select(MovementState)).all()}
    assert after == before, "run_analysis wrote current_load — Fork 7c / Option-C violation"

    # The engine DID earn an advance (so the never-wrote-current_load check above
    # is meaningful) — but a clean advance now manifests as an earned load STEP
    # (pending_load_delta), NOT a tier bump. Re-pointed (K2): current_increment_tier
    # is the step-SIZE index and a clean advance must never touch it; the earned
    # advance is staged in pending_load_delta for commit_session to apply.
    states = db.exec(select(MovementState)).all()
    assert any(ms.pending_load_delta is not None for ms in states), (
        "engine did not earn any advance (pending_load_delta) on a clean T1 session"
    )
