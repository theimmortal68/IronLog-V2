"""test_rule_wiring.py — wire Movement.progression_rule from the authoritative YAML.

The progression engine (`advance()`) dispatches on `Movement.progression_rule` and
NO-OPS when it is None. Every seeded movement shipped with progression_rule=None, so
the engine was dormant: it computed e1RM but never advanced anything. These tests
prove the rules are now wired (coverage) and that the engine consequently FIRES —
a clean top-of-range RPE-8 Bench session now advances the increment tier (0 -> 1),
where before the fix it stayed at 0.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

from sqlmodel import select

from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, ProgressionRule, Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import TierExercise
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


# ---------------------------------------------------------------------------
# Coverage: every programmed movement carries its YAML-derived progression_rule
# ---------------------------------------------------------------------------

def test_every_programmed_movement_has_progression_rule(gen_db):
    """After seeding, EVERY D1-D6 TierExercise movement has a non-None
    progression_rule (the engine can no longer no-op on any programmed slot)."""
    programmed_ids = {te.movement_id for te in gen_db.exec(select(TierExercise)).all()}
    assert programmed_ids, "no TierExercise rows seeded"
    movements = {m.id: m for m in gen_db.exec(select(Movement)).all()}
    missing = [movements[mid].name for mid in programmed_ids
               if movements[mid].progression_rule is None]
    assert missing == [], f"programmed movements with no progression_rule: {missing}"


def test_named_movements_map_to_expected_rules(gen_db):
    """Spot-check one movement per distinct ProgressionRule family."""
    by_name = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    expected = {
        "Bench Press [PB]":            ProgressionRule.RPE_8_STANDARD.value,
        # 2026-07-26: D1's Pull-up gained a real assist_ladder (3 stacked
        # 20lb bands) -- switched from tracking-only PULL_UP_ROLLING_MAX to
        # ASSISTANCE_REDUCTION (drop a band at 3x12). D4/D6 moved to a
        # separate movement ("Wide-Grip Pull-up [TOWER]"), which keeps
        # PULL_UP_ROLLING_MAX -- see test_derive_movement_rules_is_internally_consistent.
        # 2026-08-10 (STAB maintenance-block redesign): D1's pull-up slot
        # itself switched to Wide-Grip Pull-up [TOWER] (unassisted dead-hang,
        # PULL_UP_ROLLING_MAX), so "Pull-up [TOWER + TUBES]" is no longer
        # programmed on ANY day and carries no wired progression_rule.
        # ASSISTANCE_REDUCTION's spot-check moves to "Dips [TOWER + TUBES]"
        # (D6 d6_t1, still real and wired).
        "Dips [TOWER + TUBES]":        ProgressionRule.ASSISTANCE_REDUCTION.value,
        "Hip Thrust [HIP_THRUST]":     ProgressionRule.RULE_DRIVEN.value,
        "Face-Up Incline Knee Raise":  ProgressionRule.INCLINE_REDUCTION.value,
        "Cable V-Bar Pushdown [FT]":   ProgressionRule.SINGLE_SESSION.value,
        "Reverse Hyper Recovery [REV_HYPER]": ProgressionRule.FIXED_LOAD.value,
        "Light Reverse Hyper [REV_HYPER]": ProgressionRule.REP_LADDER.value,
        "Ab Wheel [WHEEL]":            ProgressionRule.REP_LADDER.value,
        # 2026-07-24: converted from ASSISTANCE_REDUCTION to RPE_8_STANDARD
        # (bodyweight-then-load double progression). 2026-07-26: D1 Pull-up
        # re-added ASSISTANCE_REDUCTION to the program (see above), so this
        # entry no longer needs to carry that rule family's spot-check.
        "Reverse Nordic Curl [GHR]":   ProgressionRule.RPE_8_STANDARD.value,
        # 2026-07-26: Dragon Flag replaced by "PureTorque Pro Rotation" in
        # D4's T3 GS (athlete directive) -- no longer programmed on any day,
        # so it carries no wired progression_rule. BODY_POSITION is now an
        # unused rule family (no spot-check example needed here).
        "PureTorque Pro Rotation":     ProgressionRule.RPE_8_STANDARD.value,
    }
    for name, rule in expected.items():
        assert by_name[name].progression_rule == rule, (
            f"{name}: expected {rule}, got {by_name[name].progression_rule!r}"
        )


def test_derive_movement_rules_is_internally_consistent():
    """The derivation itself does not raise — no movement maps to two different
    ProgressionRule values across its program slots, and every YAML rule string
    resolves to a real enum member (halt-and-flag would raise otherwise)."""
    from ironlog.generation.rule_wiring import derive_movement_rules
    rules = derive_movement_rules()
    # Hip Thrust carries rule_driven (D2/D5) AND rule_driven_fixed_increment (D6),
    # both of which resolve to RULE_DRIVEN — the consistency check must accept this.
    assert rules["Hip Thrust [HIP_THRUST]"] == ProgressionRule.RULE_DRIVEN
    # 2026-08-10 (STAB maintenance-block redesign): D1's pull-up slot itself
    # switched to Wide-Grip Pull-up [TOWER] (unassisted dead-hang), so
    # "Pull-up [TOWER + TUBES]" is no longer programmed on any day and has no
    # entry in `rules` at all (derive_movement_rules only maps movements that
    # are actually wired to a slot). "Dips [TOWER + TUBES]" (D6 d6_t1) is now
    # the real, wired ASSISTANCE_REDUCTION example.
    assert "Pull-up [TOWER + TUBES]" not in rules
    assert rules["Dips [TOWER + TUBES]"] == ProgressionRule.ASSISTANCE_REDUCTION
    assert rules["Wide-Grip Pull-up [TOWER]"] == ProgressionRule.PULL_UP_ROLLING_MAX


def test_wiring_is_idempotent(gen_db):
    """Re-running the wiring routine changes nothing the second time."""
    from ironlog.generation.rule_wiring import wire_progression_rules
    counts = wire_progression_rules(gen_db)   # already wired at seed time -> 0 changed
    assert counts["changed"] == 0, f"second wiring pass changed {counts['changed']} rows"


# ---------------------------------------------------------------------------
# End-to-end: the dormant engine now FIRES (tier advances on a clean session)
# ---------------------------------------------------------------------------

def _log_clean_session(db, *, day_role, movement_name, session_id,
                        n_sets, reps, target_reps_high, target_rpe=8.0,
                        rep_low=6, feedback=FeedbackTap.ON_TARGET,
                        session_date=date(2026, 7, 6), label="T1", actual_load=155.0):
    """Plant one COMPLETED session with `n_sets` clean working sets on a real
    seeded movement (mirrors conftest.logged_session_id / test_run_analysis_progression).

    actual_load defaults to 155.0 (Bench's seeded baseline, 2026-08-10 STAB
    maintenance-block value -- the original caller)
    -- pass the movement's own seeded current_load explicitly for any other
    movement, so the logged performance represents hitting the prescription
    cleanly rather than an incidental (and, since L's load ratchet, meaningful)
    mismatch against a different movement's baseline."""
    mv = db.exec(select(Movement).where(Movement.name == movement_name)).one()
    sess = IronSession(id=session_id, date=session_date, day_role=day_role,
                       phase="CUT", status=SessionStatus.COMPLETED)
    db.add(sess)
    db.flush()
    grp = ExerciseGroup(session_id=sess.id, order_index=0,
                        group_type=GroupType.STRAIGHT, label=label)
    db.add(grp)
    db.flush()
    pex = PlannedExercise(group_id=grp.id, movement_id=mv.id, order_index=0,
                          scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    db.add(pex)
    db.flush()
    for i in range(n_sets):
        ps = PlannedSet(planned_exercise_id=pex.id, set_index=i, set_role=SetRole.WORKING,
                        target_rpe=target_rpe, target_reps_low=rep_low,
                        target_reps_high=target_reps_high)
        db.add(ps)
        db.flush()
        db.add(SetLog(planned_set_id=ps.id, session_id=sess.id, movement_id=mv.id,
                      set_index=i, actual_load=actual_load, actual_reps=reps,
                      feedback_tap=feedback, is_warmup=False))
    db.commit()
    return mv.id


def test_clean_bench_session_earns_load_step(gen_db):
    """THE proof the dormant engine is now live AND correctly raises the load.

    Seed the Phase-1 calibrated baselines (Bench = 155 lb, 2026-08-10 STAB
    maintenance-block value, tier 0), log a clean
    top-of-range RPE-8 Bench session (all working sets at rep_high=8, ON_TARGET),
    run_analysis. A clean advance must EARN a load step (pending_load_delta = the
    coarse increment 5.0) and leave current_increment_tier UNCHANGED at 0.

    Re-pointed (K2): this test previously asserted current_increment_tier == 1,
    which encoded a semantics bug — current_increment_tier is the step-SIZE index
    (docs/04: steps DOWN on stall, up only on breakthrough), NOT an advance
    counter. A clean session must ADD load (pending_load_delta), not shrink the
    step. The engine still fires (was dormant with progression_rule=None); it now
    fires with the correct effect.
    """
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)

    bench = gen_db.exec(
        select(Movement).where(Movement.name == "Bench Press [PB]")
    ).one()
    st0 = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == bench.id,
            MovementState.day_id == "D1 Upper Push",
        )
    ).one()
    assert st0.current_increment_tier == 0
    assert st0.current_load == 155
    assert st0.pending_load_delta is None

    _log_clean_session(gen_db, day_role="D1 Upper Push",
                       movement_name="Bench Press [PB]", session_id=9001,
                       n_sets=3, reps=8, target_reps_high=8, label="T1")

    run_analysis(9001, gen_db, WEEK_KEYER)

    st1 = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == bench.id,
            MovementState.day_id == "D1 Upper Push",
        )
    ).one()
    assert st1.pending_load_delta == 5.0, (
        "clean RPE-8 T1 Bench session must earn the coarse increment (5.0) as a "
        "pending load step — the engine advances by ADDING load"
    )
    assert st1.current_increment_tier == 0, (
        "current_increment_tier is the step-SIZE index — a clean advance must NOT "
        "bump it (that would shrink the step, the old backwards behavior)"
    )
    assert st1.current_load == 155, "run_analysis never writes current_load (two-writer boundary)"
    assert st1.active_rule == ProgressionRule.RPE_8_STANDARD.value


def test_second_rule_type_single_session_advances(gen_db):
    """Prove the wiring is not Bench-specific: a single_session movement
    (Cable V-Bar Pushdown, D6 GS3) earns a load step after ONE qualifying session.

    Re-pointed (K2): was asserting current_increment_tier == 1 (the same step-size
    semantics bug). A single-session clean advance earns pending_load_delta (the
    coarse increment), tier untouched.
    """
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)

    vbar = gen_db.exec(
        select(Movement).where(Movement.name == "Cable V-Bar Pushdown [FT]")
    ).one()
    assert vbar.progression_rule == ProgressionRule.SINGLE_SESSION.value
    ladder = vbar.increment_ladder or []
    expected_step = ladder[0] if ladder else None

    _log_clean_session(gen_db, day_role="D6 Weak Points",
                       movement_name="Cable V-Bar Pushdown [FT]", session_id=9002,
                       n_sets=3, reps=12, target_reps_high=12, rep_low=8, label="GS3",
                       actual_load=60.0)  # matches its own seeded baseline -- a clean, on-script session

    run_analysis(9002, gen_db, WEEK_KEYER)

    st = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == vbar.id,
            MovementState.day_id == "D6 Weak Points",
        )
    ).one()
    assert st.pending_load_delta == expected_step, (
        "single_session must earn a load step (increment_ladder[0]) after one clean session"
    )
    assert st.current_increment_tier == 0, "single_session clean advance must NOT bump the step-size tier"
    assert st.active_rule == ProgressionRule.SINGLE_SESSION.value
