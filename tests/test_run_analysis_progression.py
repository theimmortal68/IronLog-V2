"""Tests for the progression engine wired into run_analysis (Task 6).

Covers: single-session T1 advance, two-session accessory confirmation,
stall-signal emission + clear-on-advance, the unilateral both-sides-AND
gate, and the fallback invariant (a raising engine step leaves that
movement's earned-state fields untouched and does not abort the analysis).

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date, datetime, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.enums import (
    CalibrationStatus, FeedbackTap, GroupType, Objective, Phase, ProgressionRule,
    Scheme, SetRole,
)
from ironlog.models.library import E1rmHistory, EngineState, Movement, MovementState, PhasePolicy
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_common(db, *, phase=Phase.CUT):
    db.add(EngineState(id=1, current_phase=phase))
    db.add(PhasePolicy(
        phase=phase,
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
    kw.setdefault("increment_ladder", [2.5, 5.0])
    db.add(Movement(id=movement_id, name=f"Movement {movement_id}",
                     base_name=f"Movement {movement_id}", **kw))


def _seed_session(db, session_id, movement_id, *, label, day_role="Upper A",
                   target_reps_low=5, target_reps_high=8, target_rpe=8.0,
                   actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET,
                   actual_load=135.0, side_actual_reps=None,
                   session_date=date(2026, 1, 7)):
    """Seed one Session with one working set (or two, for unilateral pairs).

    side_actual_reps: if given, a second PlannedSet/SetLog pair sharing the
    same set_index (the unilateral "other side"), with the given actual_reps.
    """
    db.add(IronSession(id=session_id, date=session_date, day_role=day_role, phase="CUT"))
    grp_id = session_id * 10
    db.add(ExerciseGroup(
        id=grp_id, session_id=session_id, order_index=0,
        group_type=GroupType.STRAIGHT, label=label,
    ))
    pex_id = grp_id
    db.add(PlannedExercise(
        id=pex_id, group_id=grp_id, movement_id=movement_id, order_index=0,
        scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS,
    ))
    ps_id = pex_id
    db.add(PlannedSet(
        id=ps_id, planned_exercise_id=pex_id, set_index=0, set_role=SetRole.WORKING,
        target_rpe=target_rpe, target_reps_low=target_reps_low, target_reps_high=target_reps_high,
    ))
    db.add(SetLog(
        planned_set_id=ps_id, session_id=session_id, movement_id=movement_id, set_index=0,
        actual_load=actual_load, actual_reps=actual_reps,
        feedback_tap=feedback_tap, is_warmup=False,
    ))
    if side_actual_reps is not None:
        ps_id2 = ps_id + 1
        db.add(PlannedSet(
            id=ps_id2, planned_exercise_id=pex_id, set_index=0, set_role=SetRole.WORKING,
            target_rpe=target_rpe, target_reps_low=target_reps_low, target_reps_high=target_reps_high,
        ))
        db.add(SetLog(
            planned_set_id=ps_id2, session_id=session_id, movement_id=movement_id, set_index=0,
            actual_load=actual_load, actual_reps=side_actual_reps,
            feedback_tap=feedback_tap, is_warmup=False,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# (a) T1 clean session advances tier + sets active_rule
# ---------------------------------------------------------------------------

def test_t1_clean_session_advances_and_sets_active_rule():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1, progression_rule=ProgressionRule.RPE_8_STANDARD.value)
        db.add(MovementState(movement_id=1, calibration_status=CalibrationStatus.MEASURED,
                              current_load=135.0))
        db.commit()
        _seed_session(db, 1, 1, label="T1")

        run_analysis(1, db, WEEK_KEYER)

        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.current_increment_tier == 1
        assert st.active_rule == ProgressionRule.RPE_8_STANDARD.value
        assert st.day_id == "Upper A"


# ---------------------------------------------------------------------------
# (b) accessory (T2 GS, confirmation_window=2) needs two clean sessions
# ---------------------------------------------------------------------------

def test_accessory_needs_two_clean_sessions_to_advance():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1, progression_rule=ProgressionRule.RPE_8_STANDARD.value)
        db.add(MovementState(movement_id=1, calibration_status=CalibrationStatus.MEASURED,
                              current_load=50.0))
        db.commit()

        _seed_session(db, 1, 1, label="T2 GS", session_date=date(2026, 1, 7))
        run_analysis(1, db, WEEK_KEYER)
        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.current_increment_tier == 0, "one clean session must not be enough for an accessory"
        assert st.consecutive_advance_count == 1

        _seed_session(db, 2, 1, label="T2 GS", day_role="Upper A", session_date=date(2026, 1, 14))
        run_analysis(2, db, WEEK_KEYER)
        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.current_increment_tier == 1, "second clean session should clear the streak and advance"


# ---------------------------------------------------------------------------
# (c) stall signal fires (PLATEAU via flat e1RM trend) and clears on advance
# ---------------------------------------------------------------------------

def test_stall_signal_fires_and_clears_on_advance():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1, progression_rule=ProgressionRule.RPE_8_STANDARD.value,
                       primary_muscle=None)
        db.add(MovementState(movement_id=1, calibration_status=CalibrationStatus.MEASURED,
                              current_load=135.0))
        # Flat PROGRESS e1RM history (plateau window) predating either session below.
        for i, sid in enumerate((100, 101, 102)):
            db.add(IronSession(id=sid, date=date(2025, 12, i + 1), day_role="Upper A", phase="CUT"))
            db.add(E1rmHistory(
                movement_id=1, session_id=sid, e1rm=200.0, objective=Objective.PROGRESS,
                phase=Phase.CUT, anchor_load=190.0, anchor_reps=5, anchor_rpe=8.0,
                computed_at=datetime(2025, 12, i + 1, tzinfo=timezone.utc),
            ))
        db.commit()

        # Session 1: NEITHER outcome (doesn't advance, doesn't drop tier) so the
        # stall signal has a chance to be written without other churn.
        _seed_session(db, 1, 1, label="T1", target_reps_low=5, target_reps_high=8,
                      actual_reps=6, session_date=date(2026, 1, 7))
        run_analysis(1, db, WEEK_KEYER)
        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.stall_signal is not None, "flat e1RM trend should emit a PLATEAU stall signal"
        assert st.stall_signal["stall_type"] == "PLATEAU"

        # Session 2 on the same (movement, day): clean CEILING hit -> T1 advances
        # in one session -> stall signal must clear to None regardless.
        _seed_session(db, 2, 1, label="T1", day_role="Upper A", actual_reps=8,
                      session_date=date(2026, 1, 14))
        run_analysis(2, db, WEEK_KEYER)
        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.current_increment_tier == 1
        assert st.stall_signal is None, "stall signal must clear when the movement advances"


# ---------------------------------------------------------------------------
# (d) unilateral both-sides-AND: one side hits, the other doesn't -> no advance
# ---------------------------------------------------------------------------

def test_unilateral_both_sides_and_gate():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1, progression_rule=ProgressionRule.RPE_8_STANDARD.value,
                       unilateral=True)
        # Pre-seed a nonzero streak so a reset to 0 proves the engine actually
        # ran this session (rather than the assertion trivially holding at
        # the untouched default of 0).
        db.add(MovementState(movement_id=1, calibration_status=CalibrationStatus.MEASURED,
                              current_load=20.0, consecutive_advance_count=3))
        db.commit()
        # side A hits (actual_reps=8 >= 8), side B misses (actual_reps=5 < 8)
        _seed_session(db, 1, 1, label="T1", actual_reps=8, side_actual_reps=5)

        run_analysis(1, db, WEEK_KEYER)

        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.current_increment_tier == 0, "one side missing target must block the advance"
        assert st.consecutive_advance_count == 0, "any miss resets the streak — proves the engine ran"
        assert st.active_rule == ProgressionRule.RPE_8_STANDARD.value


# ---------------------------------------------------------------------------
# (e) fallback invariant: a raising engine step leaves state untouched
# ---------------------------------------------------------------------------

def test_raising_advance_leaves_state_untouched_and_completes(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("ironlog.persistence.run_analysis.advance", _raise)

    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1, progression_rule=ProgressionRule.RPE_8_STANDARD.value)
        db.add(MovementState(movement_id=1, calibration_status=CalibrationStatus.MEASURED,
                              current_load=135.0))
        db.commit()
        _seed_session(db, 1, 1, label="T1")

        # Must not raise.
        run_analysis(1, db, WEEK_KEYER)

        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        # New engine fields stay at their dataclass/DB defaults — the advance
        # step never got to write them.
        assert st.active_rule is None
        assert st.stall_signal is None
        assert st.consecutive_advance_count == 0
        assert st.current_increment_tier == 0
        # Original analyze_session/apply_analysis bookkeeping (e1rm, ceiling
        # counters) is untouched by the try/except and still runs normally.
        assert st.e1rm is not None
        assert st.consecutive_ceiling_sessions == 1


# ---------------------------------------------------------------------------
# (f) unconfigured progression_rule: active_rule must stay None, never the
# literal string "None" (advance()'s unknown-rule fallback bug — every
# seeded movement today has progression_rule=None since live config is a
# deferred follow-on, so this path is hit on every first logged session).
# ---------------------------------------------------------------------------

def test_unconfigured_progression_rule_leaves_active_rule_none_not_stringified():
    engine = _make_engine()
    with Session(engine) as db:
        _seed_common(db)
        _seed_movement(db, 1)  # no progression_rule kwarg -> defaults to None
        db.add(MovementState(movement_id=1, calibration_status=CalibrationStatus.MEASURED,
                              current_load=135.0))
        db.commit()
        _seed_session(db, 1, 1, label="T1")

        run_analysis(1, db, WEEK_KEYER)

        st = db.exec(select(MovementState).where(MovementState.movement_id == 1)).one()
        assert st.active_rule is None, "must be None, not the stringified \"None\""
        # e1rm bookkeeping still runs normally even with no progression rule configured.
        assert st.e1rm is not None
        assert st.consecutive_ceiling_sessions == 1
