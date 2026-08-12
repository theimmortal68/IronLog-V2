"""test_ht_generate_banded.py — Task 5b: banded HT must survive the REAL
generate_session path.

Task 4 seeds banded HT (`ht_band_config=[orange_id]`) for D5/D6 (formerly
also D2 — D2's Hip Thrust T1b tier was removed entirely, 2026-08-11 STAB
maintenance-block redesign Task 2) via seed_movement_baselines. But
build_validation_context (repair.py) left ValidationContext.band_bottom_lb
EMPTY, so _check_ht_safety's HT_BAND_NOT_REGISTERED fired for every banded
HT set -> structurally-invalid session -> fallback -> raise. This proves the
full generate_session path now produces a valid, non-fallback session for
both remaining banded HT days.

Expected plates are the SEEDED current values (2026-07-06 athlete directive:
Week 1 prescribes exactly the seeded setup — no auto-advance at prescription;
see test_week1_prescribes_seeded_current_setup). The assembler prescribes the
current seeded setup on the planned sets and STAGES the next setup for commit,
so Week 1 shows D5=205+Orange, D6=155+Orange verbatim. D5's seeded Orange
bottom (205+18=223) is exactly the case that motivates raising
ValidationContext.ht_bottom_clamp 220->225 — this test proves the seeded
setup no longer HT_BAND_NOT_REGISTERED/HT_BOTTOM_OVER_LIMIT rejects anywhere
along the path (223 <= 225), for either remaining day's band assignment.

2026-08-12 (STAB maintenance-block redesign, Task 4): D5's Hip Thrust T1b
tier was removed entirely (2nd of 3 removals across this redesign, D6 still
to come) -- D5 no longer has a real Hip Thrust TierExercise or a
seed_movement_baselines-populated MovementState (the old d5_t1b BASELINES
entry is gone). Every test in this file that needs D5's INDEPENDENT (not
D6-derived) HT advance behavior now attaches a synthetic, test-only, PLAIN
Hip-Thrust TierExercise onto D5's real ProgramDay via `_seed_synthetic_d5_ht`
below (mirrors test_ht_composite_wiring.py's `_synthetic_plain_ht_slot` +
test_ht_unification.py's `_synthetic_ht_slot` pattern) and manually seeds its
MovementState at the same 205+Orange values the old real d5_t1b baseline
used, instead of relying on seed_movement_baselines to populate D5's row.
2026-08-12 (STAB maintenance-block redesign, Task 5): D6's real d6_g1c slot
is ALSO removed entirely now -- 3rd and final Hip Thrust removal across
this redesign (D2 Task 2, D5 Task 4, D6 here). Every test below that
exercised D6's real slot now uses `_seed_synthetic_d6_ht` (same pattern as
`_seed_synthetic_d5_ht`), seeded at D6's old real baseline (155 + #0
Orange). Unlike D5's synthetic slot, D6's synthetic slot is PLAIN (not
derived), which is fine for these tests -- see test_ht_composite_wiring.py's
module docstring for why a DERIVED slot couldn't stand in for D5's
independent-advance tests specifically; that reasoning doesn't block D6's
synthetic replacement here since these tests only check D6's PRESCRIBED
(current) setup, not an independently-advanced one.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from datetime import date

from sqlmodel import select

from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import generate_session
from ironlog.generation.proposer import StubProposer
from ironlog.generation.repair import build_validation_context
from ironlog.generation.skeleton import lay_skeleton
from ironlog.engine.validator import RuleCode, validate
from ironlog.models.enums import FeedbackTap, GroupType, Objective, Scheme, SessionStatus, SetRole
from ironlog.models.library import BandPair, Movement, MovementState
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog
from ironlog.persistence.run_analysis import run_analysis

from tests.test_ht_composite_wiring import _synthetic_plain_ht_slot

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731

D5_HT_SLOT_ID = "test_generate_banded_d5_ht"


def _seed_synthetic_d5_ht(gen_db, plates=205.0, band_config=None):
    """Attach a synthetic, plain Hip-Thrust TierExercise to D5's real
    ProgramDay and seed its MovementState at the given setup -- replaces
    the old real d5_t1b slot + BASELINES entry, both removed in Task 4. See
    module docstring."""
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    te = _synthetic_plain_ht_slot(gen_db, "D5 Lower B", ht_mv.id, D5_HT_SLOT_ID)
    st = MovementState(
        movement_id=ht_mv.id, day_id="D5 Lower B",
        ht_plates=plates, ht_band_config=list(band_config or []),
    )
    gen_db.add(st)
    gen_db.commit()
    return te, ht_mv


D6_HT_SLOT_ID = "test_generate_banded_d6_ht"


def _seed_synthetic_d6_ht(gen_db, plates=155.0, band_config=None):
    """Attach a synthetic, plain Hip-Thrust TierExercise to D6's real
    ProgramDay and seed its MovementState at the given setup.

    2026-08-12 (STAB maintenance-block redesign, Task 5): D6's real d6_g1c
    slot (the LAST real Hip Thrust TierExercise anywhere in the program) is
    removed entirely -- 3rd and final Hip Thrust removal across this
    redesign. Replaces the old real d6_g1c slot + BASELINES entry (155 +
    #0 Orange) the same way _seed_synthetic_d5_ht replaced D5's in Task 4.
    """
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    te = _synthetic_plain_ht_slot(gen_db, "D6 Weak Points", ht_mv.id, D6_HT_SLOT_ID)
    st = MovementState(
        movement_id=ht_mv.id, day_id="D6 Weak Points",
        ht_plates=plates, ht_band_config=list(band_config or []),
    )
    gen_db.add(st)
    gen_db.commit()
    return te, ht_mv


def _stage_clean_ht_advance(db, movement_id, day_role, plates, config, week_keyer):
    session = IronSession(
        date=date(2026, 7, 20),
        day_role=day_role,
        phase="CUT",
        status=SessionStatus.COMPLETED,
    )
    db.add(session)
    db.flush()

    group = ExerciseGroup(
        session_id=session.id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        label="T1",
    )
    db.add(group)
    db.flush()

    exercise = PlannedExercise(
        group_id=group.id,
        movement_id=movement_id,
        order_index=0,
        scheme=Scheme.STRAIGHT,
        objective=Objective.PROGRESS,
    )
    db.add(exercise)
    db.flush()

    for i in range(3):
        planned_set = PlannedSet(
            planned_exercise_id=exercise.id,
            set_index=i,
            set_role=SetRole.WORKING,
            target_reps_low=8,
            target_reps_high=8,
            target_rpe=8.0,
            target_plates=plates,
            band_config=list(config),
        )
        db.add(planned_set)
        db.flush()
        db.add(SetLog(
            planned_set_id=planned_set.id,
            session_id=session.id,
            movement_id=movement_id,
            set_index=i,
            actual_reps=8,
            feedback_tap=FeedbackTap.ON_TARGET,
            actual_plates=plates,
            is_warmup=False,
        ))
    db.commit()
    run_analysis(session.id, db, week_keyer)


def test_banded_ht_generates_valid_all_days(gen_db):
    seed_movement_baselines(gen_db)
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    _seed_synthetic_d5_ht(gen_db, plates=205.0, band_config=[orange.id])
    _seed_synthetic_d6_ht(gen_db, plates=155.0, band_config=[orange.id])
    # Seeded current setups (prescribe-current; no auto-advance at prescription).
    # 2026-08-11/2026-08-12: D2 and D5 both dropped their real Hip Thrust
    # TierExercise (Tasks 2/4); D5's is now a synthetic slot (see module docstring).
    for role, plates in [
        ("D5 Lower B", 205),
        ("D6 Weak Points", 155),
    ]:
        sk = lay_skeleton(role, gen_db)
        stub = StubProposer(program_selections(sk))
        outcome = generate_session(role, gen_db, stub, WEEK_KEYER)  # must NOT raise
        assert outcome.assembled is not None, f"{role}: no assembled session"
        assert not outcome.exhausted, f"{role}: repair loop exhausted"
        sess = outcome.assembled.session

        # Re-validate through the same context-building path to assert no HT
        # rejects survive (belt-and-suspenders on top of is_structurally_valid).
        sk2 = lay_skeleton(role, gen_db)
        ctx = resolve_context(role, sk2, gen_db, WEEK_KEYER)
        vc = build_validation_context(ctx, gen_db)
        res = validate(sess, vc)
        ht_rejects = [
            v for v in res.violations
            if v.rule in (RuleCode.HT_BAND_NOT_REGISTERED, RuleCode.HT_BOTTOM_OVER_LIMIT)
        ]
        assert ht_rejects == [], f"{role}: HT rejects {ht_rejects}"

        ht_sets = [
            ps
            for g in sess.groups
            for ex in g.exercises
            for ps in ex.planned_sets
            if ps.target_plates is not None
        ]
        assert ht_sets, f"{role}: no HT set with plates"
        assert all(ps.target_plates == plates for ps in ht_sets), (
            f"{role} plates != {plates}: {[ps.target_plates for ps in ht_sets]}"
        )


def test_week1_prescribes_seeded_current_setup(gen_db):
    """Crux (2026-07-06 athlete directive): Week 1 must prescribe EXACTLY the
    seeded current HT setup — no auto-advance at prescription. The assembler
    prescribes the CURRENT (seeded) setup; advancement to the next setup happens
    at commit (see test_commit_advances_ht_state), so Week 2 shows the advanced
    setup, but Week 1 shows the seed verbatim.

    Seeded baselines: D5 = 205 + #0 Orange (now via the synthetic slot seeded
    by _seed_synthetic_d5_ht, module docstring -- the old real d5_t1b
    BASELINES entry was removed in Task 4), D6 d6_g1c = 155 + #0 Orange
    (baseline_seed.BASELINES, unchanged). (Formerly also D2 d2_t1b = 205 +
    #0 Orange — D2's Hip Thrust T1b tier was removed entirely, 2026-08-11
    STAB maintenance-block redesign Task 2.) Before the prescribe-current
    fix, the assembler advanced at prescription time and this session showed
    the +1 setup instead (205 -> swap to Red 165; 155 -> 160) — this test
    asserts the raw seeded setup, so it fails against that old behavior and
    passes once the assembler prescribes current.

    Driven through the REAL generate_session path (banded HT validates now that
    the clamp is 225), asserting both plates AND that the band stays Orange.
    """
    seed_movement_baselines(gen_db)
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    _seed_synthetic_d5_ht(gen_db, plates=205.0, band_config=[orange.id])
    _seed_synthetic_d6_ht(gen_db, plates=155.0, band_config=[orange.id])
    for role, seeded_plates in [
        ("D5 Lower B", 205),
        ("D6 Weak Points", 155),
    ]:
        sk = lay_skeleton(role, gen_db)
        stub = StubProposer(program_selections(sk))
        outcome = generate_session(role, gen_db, stub, WEEK_KEYER)
        assert outcome.assembled is not None, f"{role}: no assembled session"
        assert not outcome.exhausted, f"{role}: repair loop exhausted"
        sess = outcome.assembled.session
        ht_sets = [
            ps
            for g in sess.groups
            for ex in g.exercises
            for ps in ex.planned_sets
            if ps.target_plates is not None
        ]
        assert ht_sets, f"{role}: no HT set with plates"
        assert all(ps.target_plates == seeded_plates for ps in ht_sets), (
            f"{role} Week-1 plates != seeded {seeded_plates}: "
            f"{[ps.target_plates for ps in ht_sets]}"
        )
        assert all(ps.band_config == [orange.id] for ps in ht_sets), (
            f"{role} Week-1 band_config != [Orange]: "
            f"{[ps.band_config for ps in ht_sets]}"
        )


def test_commit_advances_ht_state(gen_db):
    """Advancement is gated on a clean analyzed session (staged via pending_ht_plates), then persisted at commit — not unconditional at every commit: for a generated Week-1
    banded-HT session, the assembler STAGES ht_next_setup(seeded) as the
    prospective HT setup, and commit_session persists exactly that staged next
    setup — so the state moves forward one step and the NEXT session prescribes
    the advanced setup, while THIS session prescribed the seeded current
    (test_week1_prescribes_seeded_current_setup).

    Seeded D5 = 205 + Orange advances to 165 + Red (210+Orange bottom 228 > 225
    clamp forces a reconfigure to the smallest-peak band). This is the OTHER
    half of the prescribe-current fix: prescription = current, prospective /
    commit = next.

    Single day (D5, was D2 -- 2026-08-11: D2's Hip Thrust T1b tier was
    removed entirely, STAB maintenance-block redesign Task 2, so this
    exercised D5's still-live Hip Thrust slot instead; 2026-08-12 Task 4: D5's
    own Hip Thrust T1b tier was ALSO removed, so this now uses the synthetic
    D5 slot -- see module docstring). commit_session writes the HT setup to
    the day-scoped (movement_id, day_id) MovementState row via
    `_resolve_movement_state` (ironlog/generation/loop.py) -- this asserts
    against the D5-scoped row specifically, not an unscoped `.first()`,
    since D6's real d6_g1c row for the SAME movement_id now also exists and
    an unscoped lookup would be ambiguous.
    """
    from ironlog.engine.band_composite import Band, ht_next_setup
    from ironlog.generation.loop import commit_session

    seed_movement_baselines(gen_db)
    inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                 for bp in gen_db.exec(select(BandPair)).all()]
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()

    _te, ht_mv = _seed_synthetic_d5_ht(gen_db, plates=205.0, band_config=[orange.id])

    _stage_clean_ht_advance(gen_db, ht_mv.id, "D5 Lower B", 205.0, [orange.id], WEEK_KEYER)

    sk = lay_skeleton("D5 Lower B", gen_db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session("D5 Lower B", gen_db, stub, WEEK_KEYER)
    assert outcome.assembled is not None, "D5 Lower B: no assembled session"

    # The assembler STAGES the advanced (next) setup as prospective, even though
    # it PRESCRIBED the current seeded setup on the planned sets.
    exp_plates, exp_config = ht_next_setup(205, [orange.id], inventory)
    assert outcome.assembled.prospective_ht_setups[ht_mv.id] == (exp_plates, list(exp_config)), (
        f"prospective HT staged {outcome.assembled.prospective_ht_setups.get(ht_mv.id)} "
        f"!= next {(exp_plates, list(exp_config))}"
    )

    commit_session(
        outcome.assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    # commit_session persists the staged next setup (its sole write of
    # ht_plates/ht_band_config) to the D5-scoped MovementState row.
    st = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id,
                                    MovementState.day_id == "D5 Lower B")
    ).one()
    assert st.ht_plates == exp_plates, (
        f"committed ht_plates {st.ht_plates} != next {exp_plates}"
    )
    assert st.ht_band_config == list(exp_config), (
        f"committed ht_band_config {st.ht_band_config} != {exp_config}"
    )


def test_raised_clamp_allows_223_bottom_rejects_226(gen_db):
    """Directly proves the 220->225 clamp raise (user decision 2026-07-06):
    a hand-built HT set at the real D5 pre-progression bottom, 205 plates +
    Orange's real seeded 18 lb bottom = 223, must now PASS validate() (it
    REJECTed at the old 220 default). 208 plates + 18 = 226 must still REJECT
    — the raise is not a blanket removal of the safety gate.

    Uses build_validation_context(ctx, gen_db) against the real DB (real
    BandPair rows, real Hip Thrust movement) so band_bottom_lb/clamp come
    from production code, not a synthetic fixture like test_validator.py's.
    """
    sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", sk, gen_db, WEEK_KEYER)
    vc = build_validation_context(ctx, gen_db)
    assert vc.ht_bottom_clamp == 225.0

    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    hip_thrust = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    assert orange.id in vc.band_bottom_lb and vc.band_bottom_lb[orange.id] == orange.bottom_lb

    def _session(target_plates):
        ps = PlannedSet(planned_exercise_id=0, set_index=0, set_role=SetRole.WORKING,
                         target_plates=target_plates, band_config=[orange.id])
        ex = PlannedExercise(group_id=0, movement_id=hip_thrust.id, order_index=0,
                             scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN,
                             planned_sets=[ps])
        group = ExerciseGroup(session_id=0, order_index=0, group_type=GroupType.STRAIGHT,
                              rounds=1, exercises=[ex])
        return IronSession(date=date(2026, 1, 1), day_role="D5 Lower B", phase="CUT", groups=[group])

    res_223 = validate(_session(205.0), vc)  # 205 + 18 = 223
    ht_rejects_223 = [v for v in res_223.rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT]
    assert ht_rejects_223 == [], f"223 bottom should PASS under 225 clamp: {ht_rejects_223}"

    res_226 = validate(_session(208.0), vc)  # 208 + 18 = 226
    ht_rejects_226 = [v for v in res_226.rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT]
    assert len(ht_rejects_226) == 1, f"226 bottom should still REJECT under 225 clamp: {ht_rejects_226}"
    assert "226" in ht_rejects_226[0].message and "225" in ht_rejects_226[0].message


def test_load_override_bumps_ht_plates_day_scoped(gen_db):
    """Spec 03: a note-apply LOAD override on D5 Hip Thrust's slot must actually
    bump its PRESCRIBED plates (205 + 1 = 206) -- before this fix, the assembler's
    HT branch resolved plates from MovementState.ht_plates and silently ignored
    the overridden scalar `load` entirely once ht_plates was set (true for every
    HT movement post go-live), so a load-increase note had zero effect.

    load_delta is deliberately +1 (not the athlete's literal "+5"): D5's real
    seeded bottom is already 205+Orange(18)=223, two lb under the 225 clamp --
    a naive +5 plate bump (bottom 228) would correctly REJECT under the same
    hardware-safety clamp test_raised_clamp_allows_223_bottom_rejects_226
    proves elsewhere in this file. That's a real, separate product question
    (a manual LOAD override has no band-reconfiguration safety net the way
    ht_next_setup's auto-advance does) -- this test isolates spec 03's actual
    scope (does the override reach ht_plates at all) at a delta that stays
    safely under the clamp.

    Also proves the override is day-scoped by construction (a SlotMovementOverride
    is keyed on tier_exercise_id, and D5/D6 Hip Thrust -- formerly also D2,
    see the module docstring for the 2026-08-11 T1b removal and the
    2026-08-12 D5 T1b removal -- are distinct TierExercise rows, D5's now a
    synthetic slot) -- generating D6 in the same test must show its own
    unaffected seeded plates (155), not D5's overridden 206.
    """
    from ironlog.models.enums import OverrideType
    from ironlog.models.program import SlotMovementOverride, TierExercise
    from ironlog.models.session import Note

    seed_movement_baselines(gen_db)
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    d5_ht_te, _mv = _seed_synthetic_d5_ht(gen_db, plates=205.0, band_config=[orange.id])
    _seed_synthetic_d6_ht(gen_db, plates=155.0, band_config=[orange.id])

    note = Note(text="ready to go up 5lbs on Day 5")
    gen_db.add(note)
    gen_db.commit()
    gen_db.refresh(note)
    gen_db.add(SlotMovementOverride(
        tier_exercise_id=d5_ht_te.id, override_movement_id=d5_ht_te.movement_id,
        source_note_id=note.id, active=True,
        override_type=OverrideType.LOAD, load_delta=1.0,
    ))
    gen_db.commit()

    expected_plates = {"D5 Lower B": 206.0, "D6 Weak Points": 155.0}
    for role, plates in expected_plates.items():
        sk = lay_skeleton(role, gen_db)
        stub = StubProposer(program_selections(sk))
        outcome = generate_session(role, gen_db, stub, WEEK_KEYER)
        assert outcome.assembled is not None, f"{role}: no assembled session"
        sess = outcome.assembled.session
        ht_sets = [
            ps for g in sess.groups for ex in g.exercises for ps in ex.planned_sets
            if ps.target_plates is not None
        ]
        assert ht_sets, f"{role}: no HT set with plates"
        assert all(ps.target_plates == plates for ps in ht_sets), (
            f"{role} plates {[ps.target_plates for ps in ht_sets]} != expected {plates}"
        )


def test_load_override_does_not_compound_into_committed_ht_plates(gen_db):
    """Regression (found in review, before merge): a LOAD override has no
    auto-expiry -- it stays `active=True` across regenerations until manually
    reverted. The FIRST implementation of _apply_ht_load_override applied the
    override to cur_plates BEFORE computing ht_next_setup, so the overridden
    value became the basis for prospective_ht -- which commit_session persists.
    Every regenerate+commit cycle re-added the override on top of the already-
    advanced state, permanently drifting ht_plates upward by the override delta
    every single cycle instead of applying it once as a prescription-only tweak.

    Proves TWO regenerate+commit cycles with the SAME active +1 override on D5
    (seeded 205) land on exactly the plain two-step ht_next_setup trajectory
    from 205 -- i.e. the override affects only what's PRESCRIBED each session,
    never what commit_session persists as the next state.
    """
    from ironlog.engine.band_composite import Band, ht_next_setup
    from ironlog.generation.loop import commit_session
    from ironlog.models.enums import OverrideType
    from ironlog.models.library import MovementState
    from ironlog.models.program import SlotMovementOverride, TierExercise
    from ironlog.models.session import Note

    seed_movement_baselines(gen_db)
    inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                 for bp in gen_db.exec(select(BandPair)).all()]
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()

    # Ground truth: the plain (no-override) two-step trajectory from D5's
    # seeded 205 + Orange.
    true_step1_plates, true_step1_config = ht_next_setup(205.0, [orange.id], inventory)
    true_step2_plates, true_step2_config = ht_next_setup(
        true_step1_plates, true_step1_config, inventory
    )

    d5_ht_te, ht_mv = _seed_synthetic_d5_ht(gen_db, plates=205.0, band_config=[orange.id])
    note = Note(text="ready to go up on Day 5")
    gen_db.add(note)
    gen_db.commit()
    gen_db.refresh(note)
    gen_db.add(SlotMovementOverride(
        tier_exercise_id=d5_ht_te.id, override_movement_id=d5_ht_te.movement_id,
        source_note_id=note.id, active=True,
        override_type=OverrideType.LOAD, load_delta=1.0,
    ))
    gen_db.commit()

    _stage_clean_ht_advance(gen_db, ht_mv.id, "D5 Lower B", 205.0, [orange.id], WEEK_KEYER)

    # Cycle 1: generate + commit D5 (override active throughout).
    sk = lay_skeleton("D5 Lower B", gen_db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session("D5 Lower B", gen_db, stub, WEEK_KEYER)
    assert outcome.assembled.prospective_ht_setups[ht_mv.id] == (
        true_step1_plates, list(true_step1_config)
    ), (
        "cycle 1: the prospective (committed) next-setup must be computed from "
        "the PRE-override plates (205), matching the plain no-override "
        "trajectory -- the override must not leak into it"
    )
    commit_session(outcome.assembled, gen_db, approval_mode="auto", prompt={},
                   selections_dict={}, clamps=[], repairs=[], fallback_used=False)

    st_after_1 = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id,
                                    MovementState.day_id == "D5 Lower B")
    ).one()
    assert st_after_1.ht_plates == true_step1_plates, (
        f"after cycle 1, committed ht_plates {st_after_1.ht_plates} must equal the "
        f"normal one-step advance {true_step1_plates}, NOT that plus the override delta"
    )

    # Cycle 2: generate + commit D5 AGAIN, override STILL active (no auto-expiry).
    _stage_clean_ht_advance(gen_db, ht_mv.id, "D5 Lower B", true_step1_plates, true_step1_config, WEEK_KEYER)
    sk2 = lay_skeleton("D5 Lower B", gen_db)
    stub2 = StubProposer(program_selections(sk2))
    outcome2 = generate_session("D5 Lower B", gen_db, stub2, WEEK_KEYER)
    assert outcome2.assembled.prospective_ht_setups[ht_mv.id] == (
        true_step2_plates, list(true_step2_config)
    ), (
        "cycle 2: prospective next-setup must be the plain second step from "
        "cycle 1's REAL committed state, not inflated by the still-active override"
    )
    commit_session(outcome2.assembled, gen_db, approval_mode="auto", prompt={},
                   selections_dict={}, clamps=[], repairs=[], fallback_used=False)

    st_after_2 = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id,
                                    MovementState.day_id == "D5 Lower B")
    ).one()
    assert st_after_2.ht_plates == true_step2_plates, (
        f"after cycle 2 (override STILL active), committed ht_plates "
        f"{st_after_2.ht_plates} must equal a normal two-step advance from 205 "
        f"({true_step2_plates}), NOT drifted further by the override each cycle"
    )
