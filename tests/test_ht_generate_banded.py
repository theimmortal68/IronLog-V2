"""test_ht_generate_banded.py — Task 5b: banded HT must survive the REAL
generate_session path.

Task 4 seeds banded HT (`ht_band_config=[orange_id]`) for D2/D5/D6 via
seed_movement_baselines. But build_validation_context (repair.py) left
ValidationContext.band_bottom_lb EMPTY, so _check_ht_safety's
HT_BAND_NOT_REGISTERED fired for every banded HT set -> structurally-invalid
session -> fallback -> raise. This proves the full generate_session path now
produces a valid, non-fallback session for all three banded HT days.

Expected plates are the SEEDED current values (2026-07-06 athlete directive:
Week 1 prescribes exactly the seeded setup — no auto-advance at prescription;
see test_week1_prescribes_seeded_current_setup). The assembler prescribes the
current seeded setup on the planned sets and STAGES the next setup for commit,
so Week 1 shows D2=205+Orange, D5=205+Orange, D6=155+Orange verbatim. D2/D5's
seeded Orange bottom (205+18=223) is exactly the case that motivates raising
ValidationContext.ht_bottom_clamp 220->225 — this test proves the seeded
setup no longer HT_BAND_NOT_REGISTERED/HT_BOTTOM_OVER_LIMIT rejects anywhere
along the path (223 <= 225), for any of the three days' band assignments.

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
from ironlog.models.enums import GroupType, Objective, Scheme, SetRole
from ironlog.models.library import BandPair, Movement
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def test_banded_ht_generates_valid_all_days(gen_db):
    seed_movement_baselines(gen_db)
    # Seeded current setups (prescribe-current; no auto-advance at prescription).
    for role, plates in [
        ("D2 Lower A", 205),
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

    Seeded baselines (baseline_seed.BASELINES): D2 d2_t1b = 205 + #0 Orange,
    D5 d5_t1b = 205 + #0 Orange, D6 d6_g1c = 155 + #0 Orange. Before the
    prescribe-current fix, the assembler advanced at prescription time and this
    session showed the +1 setup instead (205 -> swap to Red 165; 155 -> 160) —
    this test asserts the raw seeded setup, so it fails against that old
    behavior and passes once the assembler prescribes current.

    Driven through the REAL generate_session path (banded HT validates now that
    the clamp is 225), asserting both plates AND that the band stays Orange.
    """
    seed_movement_baselines(gen_db)
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    for role, seeded_plates in [
        ("D2 Lower A", 205),
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
    """Advancement happens at COMMIT, not at prescription: for a generated Week-1
    banded-HT session, the assembler STAGES ht_next_setup(seeded) as the
    prospective HT setup, and commit_session persists exactly that staged next
    setup — so the state moves forward one step and the NEXT session prescribes
    the advanced setup, while THIS session prescribed the seeded current
    (test_week1_prescribes_seeded_current_setup).

    Seeded D2 = 205 + Orange advances to 165 + Red (210+Orange bottom 228 > 225
    clamp forces a reconfigure to the smallest-peak band). This is the OTHER
    half of the prescribe-current fix: prescription = current, prospective /
    commit = next.

    Single day (D2): commit_session writes the HT setup to the first
    MovementState row for the movement (its writer is day-agnostic — a
    pre-existing behavior out of this fix's scope), so this drives one commit and
    asserts against the prospective map + the row commit actually wrote.
    """
    from ironlog.engine.band_composite import Band, ht_next_setup
    from ironlog.generation.loop import commit_session
    from ironlog.models.library import MovementState

    seed_movement_baselines(gen_db)
    inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                 for bp in gen_db.exec(select(BandPair)).all()]
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()

    sk = lay_skeleton("D2 Lower A", gen_db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session("D2 Lower A", gen_db, stub, WEEK_KEYER)
    assert outcome.assembled is not None, "D2 Lower A: no assembled session"

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

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
    # ht_plates/ht_band_config) to the first HT MovementState row.
    st = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
    ).first()
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
