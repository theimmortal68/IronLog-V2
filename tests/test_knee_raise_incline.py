"""tests/test_knee_raise_incline.py — Fix C: Face-Up Incline Knee Raise is
bodyweight/incline, not a lb load.

Athlete feedback: knee raises are bodyweight; the only intensity lever is the
incline angle (degrees), stored in MovementState.assist_level. The movement was
mis-typed progression_mode=LADDER (reads current_load, seeded 25/10 in lb), so
the app showed "25 lb". This retypes it as ASSISTED (mirroring the already-
correct `Nordic Curl [GHR]`), moving its baseline seed and any live state from
current_load to assist_level, and proves the INCLINE_REDUCTION rule (already
wired onto Movement.progression_rule by an earlier task) can now actually fire
because assist_ladder is finally populated.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

from sqlmodel import select

from ironlog.engine.advance import advance, SessionPerf
from ironlog.engine.e1rm import implied_rir
from ironlog.generation.load_trust import load_field_for_mode
from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, ProgressionMode, ProgressionRule,
    Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731

KNEE_RAISE = "Face-Up Incline Knee Raise"


# ---------------------------------------------------------------------------
# 1. Movement config
# ---------------------------------------------------------------------------

def test_knee_raise_is_assisted_with_full_incline_ladder(gen_db):
    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    assert mv.progression_mode == ProgressionMode.ASSISTED
    assert mv.assist_ladder == [25, 20, 15, 10, 5, 0]
    assert mv.load_equipment_id is None, "still bodyweight — no load equipment"
    assert load_field_for_mode(mv.progression_mode) == "assist_level"


def test_nordic_and_reverse_nordic_unchanged(gen_db):
    """Assert-don't-change: Nordic Curl remains the ASSISTED template this task
    originally used; Reverse Nordic Curl's own progression_mode/rule have SINCE
    diverged from that template (see below) and are asserted against their
    current, correct values instead.

    Nordic Curl's progression_rule is NOT asserted here as unchanged: as of
    2026-07-22 its one remaining program slot (D5) is fixed at 15deg with no
    further progression (the athlete's genuine trained level was harder than
    the prescribed baseline; D2's slot was replaced with Leg Curl), so
    derive_movement_rules() now correctly resolves it to FIXED_LOAD, not
    INCLINE_REDUCTION -- a deliberate program-design change, not a regression.

    Reverse Nordic Curl converted from ASSISTED/ASSISTANCE_REDUCTION to
    LADDER/RPE_8_STANDARD/DOUBLE_PROGRESSION as of 2026-07-24 -- athlete
    directive: bodyweight reps 8-12, add load once 12 reps clears, on both
    D2 and D5. Also a deliberate program-design change, not a regression.
    """
    nordic = gen_db.exec(
        select(Movement).where(Movement.name == "Nordic Curl [GHR]")
    ).one()
    rev_nordic = gen_db.exec(
        select(Movement).where(Movement.name == "Reverse Nordic Curl [GHR]")
    ).one()
    assert nordic.progression_mode == ProgressionMode.ASSISTED
    assert rev_nordic.progression_mode == ProgressionMode.LADDER
    assert load_field_for_mode(nordic.progression_mode) == "assist_level"
    assert load_field_for_mode(rev_nordic.progression_mode) == "current_load"
    assert nordic.progression_rule == ProgressionRule.FIXED_LOAD.value
    assert rev_nordic.progression_rule == ProgressionRule.RPE_8_STANDARD.value


# ---------------------------------------------------------------------------
# 2. Baseline seed
# ---------------------------------------------------------------------------

def test_baselines_seed_assist_level_not_current_load(gen_db):
    """2026-08-10 (STAB maintenance-block redesign): Face-Up Incline Knee
    Raise dropped out of D1 entirely (T2 GS turnover), so D1 no longer has a
    MovementState for it at all -- only D4's remains to check."""
    from ironlog.generation.baseline_seed import seed_movement_baselines

    seed_movement_baselines(gen_db)
    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()

    d4 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id,
        MovementState.day_id == "D4 Upper Pull",
    )).one()

    assert d4.assist_level == 10.0
    assert d4.current_load is None


# ---------------------------------------------------------------------------
# 3. Generation regression: no lb load, assist-sourced value threads through
# ---------------------------------------------------------------------------

def test_generate_d4_upper_pull_knee_raise_carries_no_lb_load(gen_db):
    """Regression against the '25 lb' bug: the knee-raise slot's prescribed
    value must come from assist_level (10), NOT from any stray current_load.
    Plants a decoy current_load on the D4 state to prove the field-routing
    (not just the numeric coincidence) is fixed — before this fix, LADDER
    movements resolved off current_load, so a decoy would have leaked through.

    2026-08-10 (STAB maintenance-block redesign): retargeted from D1 to D4 --
    Face-Up Incline Knee Raise dropped out of D1's wiring entirely (T2 GS
    turnover), D4's copy (assist_ladder [10,5,0], baseline assist=10) is now
    the only live example of this movement in the program."""
    from ironlog.api.app import _make_proposer, _week_keyer
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.generation.loop import generate_session
    from ironlog.generation.skeleton import lay_skeleton

    seed_movement_baselines(gen_db)

    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    d4 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id,
        MovementState.day_id == "D4 Upper Pull",
    )).one()
    d4.current_load = 999.0   # decoy lb value — must never surface as target_load
    gen_db.add(d4)
    gen_db.commit()

    sk = lay_skeleton("D4 Upper Pull", gen_db)
    proposer = _make_proposer(sk)
    outcome = generate_session("D4 Upper Pull", gen_db, proposer, _week_keyer)
    assert outcome.assembled is not None, (
        f"D4 Upper Pull: generation exhausted (rejections: {outcome.rejections})"
    )

    exercises = [
        ex
        for g in outcome.assembled.session.groups
        for ex in g.exercises
        if ex.movement_id == mv.id
    ]
    assert exercises, f"{KNEE_RAISE} did not appear in the generated D4 session"
    for ex in exercises:
        assert ex.planned_sets, "knee-raise slot has no planned sets"
        for ps in ex.planned_sets:
            assert ps.target_load == 10.0, (
                f"knee-raise planned set target_load={ps.target_load!r}, expected "
                "10.0 (the D4 incline degrees from assist_level) — not the decoy "
                "current_load(999) and not a fabricated lb floor"
            )


# ---------------------------------------------------------------------------
# 4. Progression: assist_level steps DOWN the incline ladder
# ---------------------------------------------------------------------------

def test_incline_reduction_advances_real_seeded_movement(gen_db):
    """Mirrors test_incline_reduction_two_session_steps_down_ladder
    (tests/test_progression_reduction.py) but drives it off the REAL seeded
    Movement row (post-fix assist_ladder=[25,20,15,10,5,0]) rather than a
    synthetic object — proof the seed.py wiring, not just the rule itself,
    is correct. A clean qualifying session neither reduces the streak nor
    the ladder immediately (window=2); the SECOND clean session steps
    assist_level 25 -> 20."""
    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    assert mv.assist_ladder == [25, 20, 15, 10, 5, 0]
    assert mv.progression_rule == ProgressionRule.INCLINE_REDUCTION.value

    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)

    st1 = MovementState(movement_id=mv.id, day_id="D1 Upper Push",
                        assist_level=25, consecutive_advance_count=0)
    r1 = advance(ProgressionRule.INCLINE_REDUCTION, st1, perf, mv, 2)
    assert r1.advanced is False and r1.consecutive_advance_count == 1

    st2 = MovementState(movement_id=mv.id, day_id="D1 Upper Push",
                        assist_level=25, consecutive_advance_count=1)
    r2 = advance(ProgressionRule.INCLINE_REDUCTION, st2, perf, mv, 2)
    assert r2.advanced is True and r2.new_assist_level == 20


def _log_clean_knee_raise_session(db, *, session_id, session_date, movement_id,
                                  day_role="D4 Upper Pull", target_load=10.0):
    """Plant one COMPLETED session with 3 clean working sets (reps hit
    rep_high, RPE 8.0 ON_TARGET) on the knee raise, in a non-T1 group (label
    'T2') so _confirmation_window resolves to 2 — mirrors
    tests/test_rule_wiring.py::_log_clean_session. Defaults to D4 Upper Pull
    (2026-08-10: Face-Up Incline Knee Raise dropped out of D1 entirely, STAB
    maintenance-block redesign's T2 GS turnover)."""
    sess = IronSession(id=session_id, date=session_date, day_role=day_role,
                       phase="CUT", status=SessionStatus.COMPLETED)
    db.add(sess)
    db.flush()
    grp = ExerciseGroup(session_id=sess.id, order_index=0,
                        group_type=GroupType.GIANT_SET, label="T2")
    db.add(grp)
    db.flush()
    pex = PlannedExercise(group_id=grp.id, movement_id=movement_id, order_index=0,
                          scheme=Scheme.REP_RATIO, objective=Objective.PROGRESS)
    db.add(pex)
    db.flush()
    for i in range(3):
        ps = PlannedSet(planned_exercise_id=pex.id, set_index=i, set_role=SetRole.WORKING,
                        target_rpe=8.0, target_reps_low=15, target_reps_high=15)
        db.add(ps)
        db.flush()
        db.add(SetLog(planned_set_id=ps.id, session_id=sess.id, movement_id=movement_id,
                      set_index=i, actual_load=target_load, actual_reps=15,
                      feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False))
    db.commit()


def test_run_analysis_end_to_end_advances_assist_level_after_two_clean_sessions(gen_db):
    """Full pipeline proof (mirrors test_clean_bench_session_earns_load_step):
    seed the calibrated baselines, log two clean D4 knee-raise sessions through
    run_analysis, and confirm MovementState.assist_level steps 10 -> 5 on the
    second one — the INCLINE_REDUCTION rule firing end-to-end now that
    assist_ladder is populated.

    2026-08-10 (STAB maintenance-block redesign): retargeted from D1 to D4 --
    Face-Up Incline Knee Raise dropped out of D1's wiring entirely (T2 GS
    turnover). D4's assist_ladder is [10,5,0] (not D1's former [25,20,15,10,5,0]
    starting point), so the expected step is 10 -> 5, not 25 -> 20."""
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.persistence.run_analysis import run_analysis

    seed_movement_baselines(gen_db)

    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    st0 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id,
        MovementState.day_id == "D4 Upper Pull",
    )).one()
    assert st0.assist_level == 10.0
    assert st0.current_load is None

    _log_clean_knee_raise_session(gen_db, session_id=9201,
                                  session_date=date(2026, 7, 6), movement_id=mv.id)
    run_analysis(9201, gen_db, WEEK_KEYER)

    st1 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id,
        MovementState.day_id == "D4 Upper Pull",
    )).one()
    assert st1.assist_level == 10.0, "first clean session builds the streak, doesn't advance yet"

    _log_clean_knee_raise_session(gen_db, session_id=9202,
                                  session_date=date(2026, 7, 8), movement_id=mv.id)
    run_analysis(9202, gen_db, WEEK_KEYER)

    st2 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id,
        MovementState.day_id == "D4 Upper Pull",
    )).one()
    assert st2.assist_level == 5.0, (
        "second clean session must step incline down the ladder (10 -> 5) "
        "via _incline_reduction"
    )
    assert st2.current_load is None, "engine must never write a lb load onto a bodyweight movement"
