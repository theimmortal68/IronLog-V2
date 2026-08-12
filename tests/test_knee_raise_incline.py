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
    2026-07-22 its one remaining program slot (D5) was fixed at 15deg with no
    further progression (the athlete's genuine trained level was harder than
    the prescribed baseline; D2's slot was replaced with Leg Curl), so
    derive_movement_rules() correctly resolved it to FIXED_LOAD, not
    INCLINE_REDUCTION -- a deliberate program-design change, not a regression.

    2026-08-12 (STAB maintenance-block redesign, Task 4): that one remaining
    D5 slot ("Assisted Nordic (eccentric)", old d5_t2c) is now ALSO removed
    entirely -- D5's T2 GS fully turned over to Nordic Max Bulgarian Split
    Squat / Nordic Curl Max [Ares] / Better Fly Kickback. "Nordic Curl [GHR]"
    is no longer programmed on ANY day, so it carries no wired
    progression_rule (None) -- same treatment as every other fully-unwired
    movement in this program (e.g. "Pull-up [TOWER + TUBES]",
    "Dragon Flag"). Its progression_mode (ASSISTED, Movement-level config)
    is unaffected and still asserted unchanged above.

    Reverse Nordic Curl converted from ASSISTED/ASSISTANCE_REDUCTION to
    LADDER/RPE_8_STANDARD/DOUBLE_PROGRESSION as of 2026-07-24 -- athlete
    directive: bodyweight reps 8-12, add load once 12 reps clears, on both
    D2 and D5. Also a deliberate program-design change, not a regression;
    still real and wired on D5 (d5_t3b, unchanged by Task 4).
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
    assert nordic.progression_rule is None
    assert rev_nordic.progression_rule == ProgressionRule.RPE_8_STANDARD.value


# ---------------------------------------------------------------------------
# 2. Baseline seed
# ---------------------------------------------------------------------------

def test_baselines_seed_assist_level_not_current_load(gen_db):
    """2026-08-10 (STAB maintenance-block redesign): Face-Up Incline Knee
    Raise dropped out of D1 entirely (T2 GS turnover), so D1 no longer has a
    MovementState for it at all -- only D4's remained to check.

    2026-08-11 (Task 3): Face-Up Incline Knee Raise now drops out of D4's
    wiring too (T2 GS fully turned over per the FINAL doc) -- it is fully
    unwired program-wide. The "d4_t2c" BASELINES entry was removed
    (ironlog/generation/baseline_seed.py), so this is now an INVERTED guard:
    seeding baselines must NOT create a MovementState for this movement on
    ANY day. The underlying orphaned production row at the old d4_t2c slot
    is left in place (never-delete-orphans), just no longer (re)seeded."""
    from ironlog.generation.baseline_seed import seed_movement_baselines

    seed_movement_baselines(gen_db)
    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()

    d4 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id,
        MovementState.day_id == "D4 Upper Pull",
    )).first()

    assert d4 is None, (
        "Face-Up Incline Knee Raise is unwired program-wide as of Task 3 -- "
        "seed_movement_baselines must not create a state for it"
    )


# ---------------------------------------------------------------------------
# 3. Generation regression: no lb load, assist-sourced value threads through
# ---------------------------------------------------------------------------

def test_generate_d4_upper_pull_does_not_include_knee_raise(gen_db):
    """2026-08-11 (STAB maintenance-block redesign, Task 3): D4's T2 GS was
    fully turned over per the FINAL doc -- Face-Up Incline Knee Raise drops
    out of D4's wiring entirely (it was already dropped from D1 in Task 1),
    making it unwired program-wide.

    This test previously regression-guarded the '25 lb' bug fix by asserting
    KNEE_RAISE's assist_level (not a stray current_load) surfaced as
    target_load through REAL D4 generation. That live path no longer exists.
    The exact same seam -- ASSISTED progression_mode routing through
    load_field_for_mode to assist_level, not current_load, in real generated
    output -- is still exercised live by
    tests/test_golive_phase1.py::test_d6_dips_resolves_seeded_assist_level
    (Dips [TOWER + TUBES], D6, target_load == assist_level == 40). That is
    now the surviving live-path proof for this class of bug.

    What this test asserts instead: KNEE_RAISE no longer appears in a real
    generated D4 session at all -- a positive guard that the turnover
    actually took effect."""
    from ironlog.api.app import _make_proposer, _week_keyer
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.generation.loop import generate_session
    from ironlog.generation.skeleton import lay_skeleton

    seed_movement_baselines(gen_db)

    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()

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
    assert not exercises, (
        f"{KNEE_RAISE} appeared in the generated D4 session, but it is unwired "
        "as of Task 3 -- generation must not include it"
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
    assist_level 25 -> 20.

    2026-08-11 (STAB maintenance-block redesign, Task 3): Face-Up Incline
    Knee Raise is now unwired program-wide (D4's T2 GS turnover drops it,
    D1 already dropped it in Task 1), so no YAML entry uses `rule:
    incline_reduction` anymore and wire_progression_rules() no longer sets
    Movement.progression_rule for it (defaults to None, per
    ironlog/generation/rule_wiring.py's "only maps movements actually wired
    to a slot" contract -- matches the "PureTorque Pro Rotation .. no
    longer programmed .. carries no wired progression_rule" and
    INCLINE_REDUCTION-is-now-unused notes in tests/test_rule_wiring.py).
    The Movement-level assist_ladder and the advance() dispatch below (which
    takes the rule as an explicit argument, not read from mv.progression_rule)
    are unaffected -- this test still proves the rule LOGIC/seed data are
    correct, just not that live wiring routes to it."""
    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    assert mv.assist_ladder == [25, 20, 15, 10, 5, 0]
    assert mv.progression_rule is None

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
    log two clean D4 knee-raise sessions through run_analysis, and confirm
    MovementState.assist_level steps 10 -> 5 on the second one — the
    INCLINE_REDUCTION rule firing end-to-end now that assist_ladder is
    populated.

    2026-08-10 (STAB maintenance-block redesign): retargeted from D1 to D4 --
    Face-Up Incline Knee Raise dropped out of D1's wiring entirely (T2 GS
    turnover). D4's assist_ladder is [10,5,0] (not D1's former [25,20,15,10,5,0]
    starting point), so the expected step is 10 -> 5, not 25 -> 20.

    2026-08-11 (STAB maintenance-block redesign, Task 3): D4's T2 GS was
    fully turned over per the FINAL doc -- Face-Up Incline Knee Raise is now
    unwired program-wide (`Movement.progression_rule` is None, per
    test_incline_reduction_advances_real_seeded_movement above), so
    run_analysis's dispatch (keyed on movement.progression_rule, see
    ironlog/persistence/run_analysis.py) can no longer fire INCLINE_REDUCTION
    for it regardless of a seeded/planted MovementState. This is now an
    INVERTED guard: run two clean sessions through run_analysis against a
    manually-inserted state (seed_movement_baselines no longer creates one --
    its BASELINES entry was removed) and assert assist_level stays put --
    proof the engine correctly no-ops on an unwired movement rather than
    silently advancing on stale rule wiring."""
    from ironlog.persistence.run_analysis import run_analysis

    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    assert mv.progression_rule is None
    gen_db.add(MovementState(movement_id=mv.id, day_id="D4 Upper Pull",
                             assist_level=10.0))
    gen_db.commit()
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
    assert st2.assist_level == 10.0, (
        "unwired movement (progression_rule is None) must NOT advance even "
        "after two clean sessions -- run_analysis's dispatch is keyed on "
        "movement.progression_rule, which is None for this movement as of "
        "Task 3's D4 T2 GS turnover"
    )
    assert st2.current_load is None, "engine must never write a lb load onto a bodyweight movement"
