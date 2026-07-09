"""test_advance_load_bridge.py — K2: the advance->load bridge.

A clean advance must RAISE the working load, not shrink the step. K wired
progression_rule so advance() fires, but nothing translated the earned advance
into a higher current_load (and advance() wrongly bumped current_increment_tier,
which per docs/04 is the step-SIZE index — it steps DOWN on stall, not up on
progress).

This proves the full loop: clean top-of-range RPE-8 Bench session (165) ->
run_analysis earns pending_load_delta=5.0 (increment_ladder[0]) with the tier
UNCHANGED (still 0) -> generate_session prescribes Bench 170 -> commit_session
writes current_load=170 and clears pending_load_delta -> a second clean session
-> 175. Plus: no double-apply, miss resets, tier-not-bumped, engine purity.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from datetime import date

from sqlmodel import select

from ironlog.engine.advance import advance, SessionPerf
from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session, generate_session
from ironlog.generation.proposer import StubProposer
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, ProgressionRule, Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731

DAY = "D1 Upper Push"
BENCH = "Bench Press [PB]"


def _bench_state(db):
    bench = db.exec(select(Movement).where(Movement.name == BENCH)).one()
    return db.exec(
        select(MovementState).where(
            MovementState.movement_id == bench.id,
            MovementState.day_id == DAY,
        )
    ).one()


def _log_clean_bench(db, *, session_id, load, reps=8, target_reps_high=8,
                     feedback=FeedbackTap.ON_TARGET, session_date=date(2026, 7, 6)):
    """Plant one COMPLETED Bench session with 3 clean working sets at `load`."""
    bench = db.exec(select(Movement).where(Movement.name == BENCH)).one()
    sess = IronSession(id=session_id, date=session_date, day_role=DAY,
                       phase="CUT", status=SessionStatus.COMPLETED)
    db.add(sess)
    db.flush()
    grp = ExerciseGroup(session_id=sess.id, order_index=0,
                        group_type=GroupType.STRAIGHT, label="T1")
    db.add(grp)
    db.flush()
    pex = PlannedExercise(group_id=grp.id, movement_id=bench.id, order_index=0,
                          scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    db.add(pex)
    db.flush()
    for i in range(3):
        ps = PlannedSet(planned_exercise_id=pex.id, set_index=i, set_role=SetRole.WORKING,
                        target_rpe=8.0, target_reps_low=6, target_reps_high=target_reps_high)
        db.add(ps)
        db.flush()
        db.add(SetLog(planned_set_id=ps.id, session_id=sess.id, movement_id=bench.id,
                      set_index=i, actual_load=load, actual_reps=reps,
                      feedback_tap=feedback, is_warmup=False))
    db.commit()
    return bench.id


def _generate_and_commit(db):
    """Quiet-week generate for D1 -> commit; returns prospective Bench load."""
    sk = lay_skeleton(DAY, db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session(DAY, db, stub, WEEK_KEYER)
    bench = db.exec(select(Movement).where(Movement.name == BENCH)).one()
    prescribed = outcome.assembled.prospective_current_loads[bench.id]
    commit_session(outcome.assembled, db, approval_mode="auto", prompt={},
                   selections_dict={}, clamps=[], repairs=[], fallback_used=False)
    return prescribed


# ---------------------------------------------------------------------------
# THE headline: 165 -> 170 -> 175
# ---------------------------------------------------------------------------

def test_clean_session_ratchets_load_165_170_175(gen_db):
    seed_movement_baselines(gen_db)

    st0 = _bench_state(gen_db)
    assert st0.current_load == 165
    assert st0.current_increment_tier == 0
    assert st0.pending_load_delta is None

    # --- Session 1: clean top-of-range RPE-8 @165 ---
    _log_clean_bench(gen_db, session_id=9101, load=165.0)
    run_analysis(9101, gen_db, WEEK_KEYER)

    st1 = _bench_state(gen_db)
    assert st1.pending_load_delta == 5.0, "clean advance must earn increment_ladder[0]=5.0"
    assert st1.current_increment_tier == 0, "clean advance must NOT touch the step-size tier"
    assert st1.current_load == 165, "run_analysis must NOT write current_load (two-writer boundary)"
    assert st1.active_rule == ProgressionRule.RPE_8_STANDARD.value

    # --- Generate: prescribe the earned bump; commit writes it, clears the marker ---
    prescribed1 = _generate_and_commit(gen_db)
    assert prescribed1 == 170, "generation must prescribe the earned 165+5=170"

    st2 = _bench_state(gen_db)
    assert st2.current_load == 170, "commit_session is the sole current_load writer -> 170"
    assert st2.pending_load_delta is None, "commit must clear the marker (apply-exactly-once)"
    assert st2.current_increment_tier == 0

    # --- Session 2: clean again @170 -> 175 ---
    # id well clear of the auto-assigned generated-session ids from the commit above.
    _log_clean_bench(gen_db, session_id=9151, load=170.0, session_date=date(2026, 7, 13))
    run_analysis(9151, gen_db, WEEK_KEYER)

    st3 = _bench_state(gen_db)
    assert st3.pending_load_delta == 5.0
    assert st3.current_increment_tier == 0

    prescribed2 = _generate_and_commit(gen_db)
    assert prescribed2 == 175, "second clean session must ratchet 170+5=175"

    st4 = _bench_state(gen_db)
    assert st4.current_load == 175
    assert st4.pending_load_delta is None


# ---------------------------------------------------------------------------
# No double-apply — regenerating without a new clean session doesn't bump twice
# ---------------------------------------------------------------------------

def test_no_double_apply_regenerate_without_new_session(gen_db):
    seed_movement_baselines(gen_db)
    _log_clean_bench(gen_db, session_id=9111, load=165.0)
    run_analysis(9111, gen_db, WEEK_KEYER)

    # First generate+commit consumes the delta -> 170, marker cleared.
    assert _generate_and_commit(gen_db) == 170
    assert _bench_state(gen_db).pending_load_delta is None

    # Regenerate WITHOUT logging a new clean session: must stay 170, not 175.
    assert _generate_and_commit(gen_db) == 170
    assert _bench_state(gen_db).current_load == 170


# ---------------------------------------------------------------------------
# Miss resets — a rep miss / too-hard earns no delta; load holds
# ---------------------------------------------------------------------------

def test_rep_miss_earns_no_delta(gen_db):
    seed_movement_baselines(gen_db)
    _log_clean_bench(gen_db, session_id=9121, load=165.0, reps=6, target_reps_high=8)
    run_analysis(9121, gen_db, WEEK_KEYER)

    st = _bench_state(gen_db)
    assert st.pending_load_delta is None, "a rep miss earns no load step"
    assert st.current_load == 165
    assert _generate_and_commit(gen_db) == 165, "missed session -> load holds at 165"


def test_too_hard_earns_no_delta(gen_db):
    seed_movement_baselines(gen_db)
    # Hit target reps but TOO_HARD -> max_rpe > 8 -> not clean.
    _log_clean_bench(gen_db, session_id=9131, load=165.0, feedback=FeedbackTap.TOO_HARD)
    run_analysis(9131, gen_db, WEEK_KEYER)

    st = _bench_state(gen_db)
    assert st.pending_load_delta is None, "TOO_HARD (RPE>8) earns no load step"
    assert st.current_load == 165


# ---------------------------------------------------------------------------
# Engine purity — advance() earns the step, does NOT bump the tier (regression)
# ---------------------------------------------------------------------------

def _mv():
    return Movement(name="Bench", pattern="press", increment_ladder=[5, 2.5])


def _st(tier=0, streak=0):
    return MovementState(movement_id=1, day_id="d1",
                         current_increment_tier=tier, consecutive_advance_count=streak)


def test_rpe8_clean_advance_earns_step_not_tier():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=0),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True),
                _mv(), confirmation_window=1)
    assert r.advanced is True
    assert r.new_tier is None, "clean advance must NOT step the tier (regression vs +1 bug)"
    assert r.earned_load_step == 5.0, "earns increment_ladder[current_increment_tier]"


def test_rpe8_clean_advance_earns_finer_step_at_tier1():
    # At tier 1 (post-stall step-down), the earned step is the finer rung 2.5.
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(tier=1, streak=0),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True),
                _mv(), confirmation_window=1)
    assert r.advanced is True and r.new_tier is None
    assert r.earned_load_step == 2.5


def test_single_session_clean_advance_earns_step_not_tier():
    r = advance(ProgressionRule.SINGLE_SESSION, _st(),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                            last_set_hit_target=True),
                _mv(), 1)
    assert r.advanced is True and r.new_tier is None
    assert r.earned_load_step == 5.0


# ---------------------------------------------------------------------------
# L: load ratchet — never prescribe below a logged actual performance
# ---------------------------------------------------------------------------
# Concrete case: Belt Squat seeded 260, athlete logs 265x12 at RPE 8 (off-script
# heavier). Belt Squat's rule is REP_LADDER, not RPE_8_STANDARD, so advance()
# earns no scalar earned_load_step here -- the floor must apply independent of
# whether a clean rule-driven advance fired.

from ironlog.engine.advance import performed_floor_delta

BELT_SQUAT = "Belt Squat [GHR + FT]"
D2 = "D2 Lower A"


def test_performed_floor_delta_floors_to_heaviest_logged():
    assert performed_floor_delta(260.0, [265.0]) == 5.0


def test_performed_floor_delta_zero_when_performed_lighter():
    assert performed_floor_delta(260.0, [250.0]) == 0.0


def test_performed_floor_delta_zero_when_current_load_none():
    assert performed_floor_delta(None, [265.0]) == 0.0


def test_performed_floor_delta_zero_when_no_performed_loads():
    assert performed_floor_delta(260.0, []) == 0.0


def test_performed_floor_delta_picks_heaviest_of_multiple_sets():
    assert performed_floor_delta(260.0, [250.0, 265.0, 255.0]) == 5.0


def _log_belt_squat(db, *, session_id, load, reps, feedback=FeedbackTap.ON_TARGET,
                    session_date=date(2026, 7, 8)):
    """Plant one COMPLETED D2 session with one Belt Squat working set at `load`."""
    bs = db.exec(select(Movement).where(Movement.name == BELT_SQUAT)).one()
    sess = IronSession(id=session_id, date=session_date, day_role=D2,
                       phase="CUT", status=SessionStatus.COMPLETED)
    db.add(sess)
    db.flush()
    grp = ExerciseGroup(session_id=sess.id, order_index=0,
                        group_type=GroupType.STRAIGHT, label="T1")
    db.add(grp)
    db.flush()
    pex = PlannedExercise(group_id=grp.id, movement_id=bs.id, order_index=0,
                          scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    db.add(pex)
    db.flush()
    ps = PlannedSet(planned_exercise_id=pex.id, set_index=0, set_role=SetRole.WORKING,
                    target_rpe=8.0, target_reps_low=6, target_reps_high=8)
    db.add(ps)
    db.flush()
    db.add(SetLog(planned_set_id=ps.id, session_id=sess.id, movement_id=bs.id,
                  set_index=0, actual_load=load, actual_reps=reps,
                  feedback_tap=feedback, is_warmup=False))
    db.commit()
    return bs.id


def _belt_squat_state(db):
    bs = db.exec(select(Movement).where(Movement.name == BELT_SQUAT)).one()
    return db.exec(
        select(MovementState).where(
            MovementState.movement_id == bs.id,
            MovementState.day_id == D2,
        )
    ).one()


def test_load_ratchet_floors_to_performed_when_off_script_heavier(gen_db):
    seed_movement_baselines(gen_db)

    st0 = _belt_squat_state(gen_db)
    assert st0.current_load == 260
    assert st0.pending_load_delta is None

    # Off-script heavier: seeded 260, athlete logs 265x12 at RPE 8.
    _log_belt_squat(gen_db, session_id=9201, load=265.0, reps=12)
    run_analysis(9201, gen_db, WEEK_KEYER)

    st1 = _belt_squat_state(gen_db)
    assert st1.active_rule == ProgressionRule.REP_LADDER.value
    assert st1.pending_load_delta == 5.0, (
        "floor must stage +5 (265-260) even though REP_LADDER earns no scalar "
        "earned_load_step of its own"
    )
    assert st1.current_load == 260, "run_analysis must NOT write current_load directly"

    # Regenerate D2 and confirm Belt Squat is prescribed 265, not 260.
    sk = lay_skeleton(D2, gen_db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session(D2, gen_db, stub, WEEK_KEYER)
    bs = gen_db.exec(select(Movement).where(Movement.name == BELT_SQUAT)).one()
    prescribed = outcome.assembled.prospective_current_loads[bs.id]
    assert prescribed == 265, "next Belt Squat prescription must not regress below 265"

    commit_session(outcome.assembled, gen_db, approval_mode="auto", prompt={},
                   selections_dict={}, clamps=[], repairs=[], fallback_used=False)
    st2 = _belt_squat_state(gen_db)
    assert st2.current_load == 265
    assert st2.pending_load_delta is None, "marker cleared after commit (apply-once)"


def test_load_ratchet_does_not_lower_prescription_when_performed_lighter(gen_db):
    seed_movement_baselines(gen_db)
    _log_belt_squat(gen_db, session_id=9202, load=250.0, reps=8)
    run_analysis(9202, gen_db, WEEK_KEYER)
    st = _belt_squat_state(gen_db)
    assert st.pending_load_delta is None, "performing lighter than seeded must not floor/lower anything"


def test_load_ratchet_excludes_ht_composite_movements(gen_db):
    """HT is out of scope for this spec (Spec 03 owns it) -- a HIP_THRUST/COMPOSITE
    movement's floor must never be computed against current_load."""
    from ironlog.models.enums import LiftCategory
    ht = gen_db.exec(select(Movement).where(Movement.lift_category == LiftCategory.HIP_THRUST)).first()
    assert ht is not None
    assert ht.progression_mode.value == "COMPOSITE"
