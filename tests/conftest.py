"""Shared pytest fixtures for the generation layer tests.

gen_db — creates an in-memory SQLite DB, seeds the library (103 movements)
         via seed.seed(), then seeds the Phase 1 program via seed_phase1_program().
         Yields the live Session so tests can query directly.

logged_session_id — depends on gen_db; plants a COMPLETED session with one tapped
         working set on Pull-up [TOWER+TUBES] (PROGRESS lift) so run_analysis
         produces an E1rmHistory row. Returns the session id.

stalled_session_db — depends on gen_db; plants a stall signal on
         Better Fly Sagittal Lat Pulldown [FT] (D1 d1_t3e, tier_role=free) via
         consecutive_failed_progressions=2. Returns the same gen_db session.
         (2026-07-26: moved off Pendlay Row - Narrow [OB] -- it was promoted
         to D1's T1b anchor slot, and should_invoke_llm only considers
         semi/free tier_role slots, so an anchor can no longer carry this
         fixture's stall signal. 2026-08-10: moved off Seated Cable Row [FT]
         (D1 d1_t4a) -- the STAB maintenance-block redesign removed D1's
         entire T4 GS tier, so Seated Cable Row no longer has any D1
         TierExercise for a stall signal to attach to; Lat Prayer at d1_t3c
         is free tier_role, same eligibility as the old semi slot. 2026-08-13:
         moved off Lat Prayer [ANDREONI + FT] -- replaced by Better Fly
         Sagittal Lat Pulldown [FT] at fresh slot d1_t3e, athlete directive;
         Lat Prayer is now fully unwired from D1, same free-tier_role
         eligibility carries to the new slot.)

Placed in conftest.py so pytest auto-discovers it for all test modules in tests/.
_gen_fixtures.py re-exports this fixture for explicit import in test modules.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib
from datetime import date

import pytest
from sqlmodel import Session, create_engine, select


@pytest.fixture
def gen_db():
    eng = create_engine("sqlite://")
    import ironlog.db as db
    db.engine = eng
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()                                    # 103-movement library
    from ironlog.generation.program_seed import seed_phase1_program
    with Session(eng) as s:
        seed_phase1_program(s)                     # the Phase 1 program prior
        yield s


@pytest.fixture
def gen_db_calibrated(gen_db):
    """gen_db + a real configured load for EVERY loaded movement (LADDER/COMPOSITE
    → current_load, ASSISTED → assist_level), confirmed just now.

    Simulates the post-wizard / post-calibration production reality: loads exist,
    so generation prescribes them (FRESH) instead of flagging needs-calibration.
    Used by the assembler/commit/cold-start gates that, after Task 3 dropped the
    silent floor fallback, must seed real loads (toward-correct) rather than rely
    on a fabricated floor.  Bodyweight movements (PROTOCOL/CONDITIONING/NONE) are
    left loadless — they legitimately carry no external load.
    """
    from datetime import datetime

    from ironlog.generation.load_trust import load_field_for_mode
    from ironlog.models.library import Movement, MovementState

    now = datetime.utcnow()
    states = {s.movement_id: s
              for s in gen_db.exec(select(MovementState)).all()}
    for m in gen_db.exec(select(Movement)).all():
        field = load_field_for_mode(m.progression_mode)
        if field is None:
            continue                       # bodyweight: no load to configure
        st = states.get(m.id)
        if st is None:
            st = MovementState(movement_id=m.id)
            gen_db.add(st)
        if getattr(st, field) is None:
            setattr(st, field, 100.0 if field == "current_load" else 0.0)
        st.confirmed_at = now
        gen_db.add(st)
    gen_db.commit()
    return gen_db


@pytest.fixture
def logged_session_id(gen_db):
    """COMPLETED session with one tapped working set on a PROGRESS lift.

    Pull-up [TOWER + TUBES] has objective_override=PROGRESS. The tapped working
    set (actual_load=10.0, reps=5, ON_TARGET, target_rpe=8.0) qualifies as an
    e1RM anchor → run_analysis produces one E1rmHistory row.
    Returns the session id (int).
    """
    from ironlog.models.enums import (
        CalibrationStatus, FeedbackTap, GroupType, Objective, Scheme,
        SessionStatus, SetRole,
    )
    from ironlog.models.library import Movement, MovementState
    from ironlog.models.session import (
        ExerciseGroup, PlannedExercise, PlannedSet,
        Session as IronSession, SetLog,
    )

    mv = gen_db.exec(
        select(Movement).where(Movement.name == "Pull-up [TOWER + TUBES]")
    ).one()

    gen_db.add(MovementState(
        movement_id=mv.id,
        calibration_status=CalibrationStatus.CALIBRATING,
        current_load=0.0,
    ))
    gen_db.flush()

    sess = IronSession(
        date=date(2026, 6, 1), day_role="D1 Upper Push",
        phase="CUT", status=SessionStatus.COMPLETED,
    )
    gen_db.add(sess)
    gen_db.flush()

    grp = ExerciseGroup(
        session_id=sess.id, order_index=0, group_type=GroupType.STRAIGHT,
    )
    gen_db.add(grp)
    gen_db.flush()

    pex = PlannedExercise(
        group_id=grp.id, movement_id=mv.id, order_index=0,
        scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS,
    )
    gen_db.add(pex)
    gen_db.flush()

    pset = PlannedSet(
        planned_exercise_id=pex.id, set_index=0, set_role=SetRole.WORKING,
        target_rpe=8.0, target_reps_low=5, target_reps_high=8,
    )
    gen_db.add(pset)
    gen_db.flush()

    gen_db.add(SetLog(
        planned_set_id=pset.id, session_id=sess.id, movement_id=mv.id,
        set_index=0, actual_load=10.0, actual_reps=5,
        feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
    ))
    gen_db.commit()

    return sess.id


@pytest.fixture
def stalled_session_db(gen_db):
    """gen_db + a stall signal on Better Fly Sagittal Lat Pulldown [FT] (D1 d1_t3e, free).

    consecutive_failed_progressions=2 >= STALL_FAILED_THRESHOLD(2) → detect_stall
    fires (failed_stalled=True) → movement added to weak_point_hints →
    slot_has_deviation_signal True for d1_t3e → should_invoke_llm True for D1.
    Yields the same gen_db session so tests can use it as a drop-in for gen_db.
    """
    from ironlog.models.library import Movement, MovementState

    mv = gen_db.exec(
        select(Movement).where(Movement.name == "Better Fly Sagittal Lat Pulldown [FT]")
    ).one()

    gen_db.add(MovementState(
        movement_id=mv.id,
        consecutive_failed_progressions=2,          # >= STALL_FAILED_THRESHOLD (2)
    ))
    gen_db.commit()
    return gen_db
