"""tests/test_timed_tier_exercise.py — spec 59: duration-based TierExercise
support (Suitcase Dreadmill Carry). Mirrors tests/test_rule_wiring.py's
clean-session-advance pattern, substituting duration for reps.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

from sqlmodel import select

from ironlog.models.enums import (
    CalibrationStatus, FeedbackTap, GroupType, Objective, ProgressionRule, Scheme,
    SessionStatus, SetRole,
)
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def _seed_state(db, movement_id, day_id, current_load):
    st = MovementState(
        movement_id=movement_id, day_id=day_id,
        calibration_status=CalibrationStatus.MEASURED,
        current_load=current_load, current_increment_tier=0,
    )
    db.add(st); db.commit(); db.refresh(st)
    return st


def _log_timed_session(db, *, day_role, movement_id, session_id, n_sets,
                        actual_duration_seconds, target_duration_low_seconds,
                        target_duration_high_seconds, actual_load, target_rpe=8.0,
                        feedback=FeedbackTap.ON_TARGET, session_date=date(2026, 9, 1),
                        label="T3 GS"):
    sess = IronSession(id=session_id, date=session_date, day_role=day_role,
                        phase="CUT", status=SessionStatus.COMPLETED)
    db.add(sess); db.flush()
    grp = ExerciseGroup(session_id=sess.id, order_index=0,
                         group_type=GroupType.GIANT_SET, label=label)
    db.add(grp); db.flush()
    pex = PlannedExercise(group_id=grp.id, movement_id=movement_id, order_index=0,
                           scheme=Scheme.DOUBLE_PROGRESSION, objective=Objective.PROGRESS)
    db.add(pex); db.flush()
    for i in range(n_sets):
        ps = PlannedSet(planned_exercise_id=pex.id, set_index=i, set_role=SetRole.WORKING,
                         target_rpe=target_rpe,
                         target_duration_low_seconds=target_duration_low_seconds,
                         target_duration_high_seconds=target_duration_high_seconds)
        db.add(ps); db.flush()
        db.add(SetLog(planned_set_id=ps.id, session_id=sess.id, movement_id=movement_id,
                       set_index=i, actual_load=actual_load,
                       actual_duration_seconds=actual_duration_seconds,
                       feedback_tap=feedback, is_warmup=False))
    db.commit()


def _suitcase_carry(db):
    """Fetch Suitcase Dreadmill Carry and set progression_rule, mirroring
    what deploy/migrations/063_...sql sets directly on the live row --
    ironlog/seed.py never sets Movement.progression_rule itself (that's
    rule_wiring.py's job for movements with real yaml wiring; this movement
    is deliberately unwired in the fresh-seed universe, same as D6's Cable
    Serratus Punch/Reach, so nothing wires it there either)."""
    mv = db.exec(select(Movement).where(Movement.name == "Suitcase Dreadmill Carry")).one()
    if mv.progression_rule != ProgressionRule.RPE_8_STANDARD.value:
        mv.progression_rule = ProgressionRule.RPE_8_STANDARD.value
        db.add(mv); db.commit(); db.refresh(mv)
    return mv


def test_clean_duration_session_earns_load_step(gen_db):
    """THE proof duration-based double progression works: a clean session
    where every working set hits duration_high_seconds (30s) at RPE within
    cap must earn a load step (pending_load_delta = increment_ladder's
    coarse rung, 5.0 per migration 063), exactly mirroring the rep-based
    RPE_8_STANDARD advance in test_rule_wiring.py::test_clean_bench_session_earns_load_step.
    """
    mv = _suitcase_carry(gen_db)
    st0 = _seed_state(gen_db, mv.id, "D2 Lower A", current_load=100.0)
    assert st0.pending_load_delta is None

    _log_timed_session(
        gen_db, day_role="D2 Lower A", movement_id=mv.id, session_id=9101,
        n_sets=3, actual_duration_seconds=30, target_duration_low_seconds=20,
        target_duration_high_seconds=30, actual_load=100.0,
    )
    run_analysis(9101, gen_db, WEEK_KEYER)

    st1 = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == mv.id, MovementState.day_id == "D2 Lower A",
        )
    ).one()
    assert st1.pending_load_delta == 5.0, (
        "all working sets hitting duration_high_seconds at RPE within cap must earn "
        "the coarse increment as a pending load step"
    )
    assert st1.current_load == 100.0, "run_analysis never writes current_load (two-writer boundary)"
    assert st1.active_rule == ProgressionRule.RPE_8_STANDARD.value


def test_duration_session_below_target_does_not_advance(gen_db):
    """A session where working sets fall short of duration_high_seconds must
    NOT earn a load step -- holds, exactly like a rep-based session that
    falls short of rep_high."""
    mv = _suitcase_carry(gen_db)
    _seed_state(gen_db, mv.id, "D2 Lower A", current_load=100.0)

    _log_timed_session(
        gen_db, day_role="D2 Lower A", movement_id=mv.id, session_id=9102,
        n_sets=3, actual_duration_seconds=22,  # short of duration_high_seconds=30
        target_duration_low_seconds=20, target_duration_high_seconds=30,
        actual_load=100.0,
    )
    run_analysis(9102, gen_db, WEEK_KEYER)

    st = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == mv.id, MovementState.day_id == "D2 Lower A",
        )
    ).one()
    assert st.pending_load_delta is None, (
        "sets that fall short of duration_high_seconds must not earn a load step"
    )


def test_duration_progression_never_touches_rep_based_movement(gen_db):
    """Regression guard: exercising the new duration path must not corrupt an
    unrelated rep-based movement's progression. Log a clean duration session
    for Suitcase Carry AND a clean rep session for Bench Press in the same
    run_analysis batch (two different sessions, run sequentially) and confirm
    each advances independently and correctly."""
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)

    bench = gen_db.exec(select(Movement).where(Movement.name == "Bench Press [PB]")).one()
    carry = _suitcase_carry(gen_db)
    _seed_state(gen_db, carry.id, "D2 Lower A", current_load=100.0)

    # Bench: clean rep-based session (mirrors test_rule_wiring.py exactly).
    sess = IronSession(id=9103, date=date(2026, 9, 1), day_role="D1 Upper Push",
                        phase="CUT", status=SessionStatus.COMPLETED)
    gen_db.add(sess); gen_db.flush()
    grp = ExerciseGroup(session_id=sess.id, order_index=0, group_type=GroupType.STRAIGHT, label="T1")
    gen_db.add(grp); gen_db.flush()
    pex = PlannedExercise(group_id=grp.id, movement_id=bench.id, order_index=0,
                           scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    gen_db.add(pex); gen_db.flush()
    for i in range(3):
        ps = PlannedSet(planned_exercise_id=pex.id, set_index=i, set_role=SetRole.WORKING,
                         target_rpe=8.0, target_reps_low=6, target_reps_high=8)
        gen_db.add(ps); gen_db.flush()
        gen_db.add(SetLog(planned_set_id=ps.id, session_id=sess.id, movement_id=bench.id,
                           set_index=i, actual_load=155.0, actual_reps=8,
                           feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False))
    gen_db.commit()
    run_analysis(9103, gen_db, WEEK_KEYER)

    # Suitcase Carry: clean duration-based session.
    _log_timed_session(
        gen_db, day_role="D2 Lower A", movement_id=carry.id, session_id=9104,
        n_sets=3, actual_duration_seconds=30, target_duration_low_seconds=20,
        target_duration_high_seconds=30, actual_load=100.0,
    )
    run_analysis(9104, gen_db, WEEK_KEYER)

    bench_state = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == bench.id, MovementState.day_id == "D1 Upper Push",
        )
    ).one()
    carry_state = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == carry.id, MovementState.day_id == "D2 Lower A",
        )
    ).one()
    assert bench_state.pending_load_delta == 5.0, (
        "Bench's rep-based advance must fire correctly, unaffected by the duration path existing"
    )
    assert carry_state.pending_load_delta == 5.0, (
        "Suitcase Carry's duration-based advance must fire correctly, independent of Bench"
    )
