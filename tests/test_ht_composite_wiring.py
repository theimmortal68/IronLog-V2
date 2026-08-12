"""test_ht_composite_wiring.py — Task 4: wire ht_next_setup into generation (Option-C).

Named tests:
  1. test_assembled_ht_carries_plates_and_config — assembler HT slot prescribes
     the CURRENT setup (plates + band_config); target_felt_peak matches
     config_peak; the NEXT setup (ht_next_setup) is staged as prospective for
     commit (prescribe-current, advance-at-commit — 2026-07-06 directive).
  2. test_commit_persists_ht_setup — commit_session (approval-time) is the sole
     writer of ht_plates/ht_band_config, mirroring current_load (Fork 7c).
  3. test_rule_driven_composite_ht_at_cap_is_noop — _rule_driven's at-cap branch
     must NOT hand a COMPOSITE/HIP_THRUST movement to _rep_ladder; the setup
     progression is assembler-resolved, not rep-ladder-resolved.
  4. test_rule_driven_non_composite_at_cap_still_hands_to_rep_ladder — regression
     guard: the pre-existing at-cap -> REP_LADDER handoff is untouched for a
     movement that is NOT COMPOSITE/HIP_THRUST (test_progression_special.py's
     "Hip Thrust" fixture uses plain defaults, so this is exercised there too;
     this test makes the boundary explicit).

NO from __future__ import annotations (project-wide constraint).
gen_db / gen_db_calibrated fixtures auto-discovered from conftest.py.

2026-08-11 (STAB maintenance-block redesign, Task 2): every "D2 Lower A"
day_role in this file was changed to "D5 Lower B" -- D2's Hip Thrust T1b
tier was removed entirely (not just the movement), so D2 no longer has any
Hip Thrust TierExercise to exercise this file's generic HT-plumbing tests
against. D5's still-live Hip Thrust slot was a drop-in replacement; none of
these tests assert anything D2-specific (rep range, Belt Squat, etc.), they
use the day purely as a vehicle to exercise HT assembly/commit logic.

2026-08-12 (STAB maintenance-block redesign, Task 4): D5's Hip Thrust T1b
tier was ALSO removed entirely (2nd of 3 Hip Thrust removals across this
redesign, D6 still to come) -- every "D5 Lower B" in this file is repointed
to "D6 Weak Points" (its d6_g1c slot), the last real Hip Thrust TierExercise
left in the program. IMPORTANT CAVEAT: d6_g1c is a DERIVED slot
(`derived_from_unified_group="main"`), not a plain independent HT-composite
slot -- run_analysis intentionally passes `band_inventory=None` for derived
slots (ironlog/persistence/run_analysis.py's `_is_derived_ht_slot` check),
so it never independently wear-gates against a retired band; its real setup
is meant to come from the unified group's derive formula, not its own
progression. That makes it unsuitable for
test_assembler_does_not_prescribe_a_retired_band specifically, which needs
a plain (non-derived, non-unified) HT slot to exercise independent wear-
gating -- there is no longer a real one anywhere in the program after this
task. That one test uses a synthetic, test-only Hip-Thrust TierExercise
(`_synthetic_plain_ht_slot`, mirrors the established pattern in
test_ht_unification.py's `_synthetic_ht_slot`) attached to D5's real
ProgramDay instead. The other 7 tests in this file exercise generic
assembly/commit mechanics unaffected by derived-vs-plain status and stay on
D6 Weak Points' real d6_g1c slot.
"""
from datetime import date

from sqlmodel import select

from ironlog.engine.advance import advance, SessionPerf
from ironlog.engine.band_composite import Band, ht_next_setup
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import (
    FeedbackTap, GroupType, LiftCategory, Objective, ProgressionMode,
    ProgressionRule, Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import BandPair, Movement, MovementState
from ironlog.models.program import ProgramDay, Tier, TierExercise, TierKind
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis


def _synthetic_plain_ht_slot(gen_db, day_role, movement_id, slot_id):
    """Attach a throwaway, PLAIN (non-derived, non-unified) Hip-Thrust
    TierExercise onto the given day_role's real ProgramDay (own Tier,
    tier_order=99 so it never collides with the day's real tiers). Mirrors
    test_ht_unification.py's _synthetic_ht_slot pattern -- used here only by
    test_assembler_does_not_prescribe_a_retired_band, which needs a slot
    that independently wear-gates against a retired band (see module
    docstring: D6's real d6_g1c is a DERIVED slot and intentionally skips
    that gating)."""
    pd = gen_db.exec(select(ProgramDay).where(ProgramDay.day_role == day_role)).one()
    tier = Tier(program_day_id=pd.id, tier_label="TEST-HT", tier_order=99,
                tier_kind=TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120)
    gen_db.add(tier)
    gen_db.flush()
    te = TierExercise(tier_id=tier.id, slot_id=slot_id, movement_id=movement_id,
                       exercise_order=1, tier_role="anchor", scheme="COMPOSITE")
    gen_db.add(te)
    gen_db.flush()
    return te


def _first_ht_working_set(assembled) -> PlannedSet:
    """The first PlannedSet carrying a non-None target_plates (the HT slot)."""
    for g in assembled.session.groups:
        for ex in g.exercises:
            for ps in ex.planned_sets:
                if ps.target_plates is not None:
                    return ps
    raise AssertionError("no HT (target_plates-bearing) set found in assembled session")


def _planned_sets_for_movement(assembled, movement_id):
    """All PlannedSets belonging to the given movement_id, regardless of
    whether target_plates is populated (unlike _first_ht_working_set, which
    specifically searches for a populated HT slot)."""
    out = []
    for g in assembled.session.groups:
        for ex in g.exercises:
            if ex.movement_id == movement_id:
                out.extend(ex.planned_sets)
    return out


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


# ---------------------------------------------------------------------------
# 1. assembler HT slot
# ---------------------------------------------------------------------------

def test_assembled_ht_carries_plates_and_config(gen_db_calibrated):
    # NOTE: uses a synthetic PLAIN (non-derived) HT slot on D5's real
    # ProgramDay, same reasoning as test_assembler_does_not_prescribe_a_
    # retired_band (see module docstring) -- this test asserts that
    # assembled.prospective_ht_setups matches a freshly-computed
    # ht_next_setup() call directly, which only holds for a slot whose
    # independent progression is actually band-gated (D6's real d6_g1c is
    # derived and skips that gating, producing a different pending value).
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    _synthetic_plain_ht_slot(gen_db, "D5 Lower B", ht_mv.id, "test_carries_d5_ht")

    baseline = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
    ).first()
    plates = (baseline.ht_plates if baseline and baseline.ht_plates is not None
              else (baseline.current_load if baseline else 155.0))
    st = MovementState(
        movement_id=ht_mv.id, day_id="D5 Lower B",
        ht_plates=plates, ht_band_config=[],
    )
    gen_db.add(st)
    gen_db.commit()
    _stage_clean_ht_advance(
        gen_db, ht_mv.id, "D5 Lower B", st.ht_plates, st.ht_band_config, wk,
    )

    sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", sk, gen_db, wk)
    sel = program_selections(sk)

    assembled = assemble(sel, sk, ctx, gen_db)

    ht_set = _first_ht_working_set(assembled)
    assert ht_set.target_plates is not None
    assert ht_set.band_config is not None
    assert ht_set.target_felt_peak is not None
    assert ht_set.target_load is None, (
        "HT sets are loaded via plates+bands, not a scalar target_load; "
        "the scalar must be cleared so the UI doesn't show a confusing "
        "'Target: Nlb' alongside the real plates/peak"
    )

    peak_by_id = {b.id: b.peak_lb for b in gen_db.exec(select(BandPair)).all()}
    assert ht_set.target_felt_peak == ht_set.target_plates + sum(
        peak_by_id[b] for b in ht_set.band_config
    )

    # Prescribe-current semantics: the planned set carries the CURRENT setup,
    # while the clean analyzed prior session staged the NEXT setup for commit.
    inv = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
           for bp in gen_db.exec(select(BandPair)).all()]
    expected_next = ht_next_setup(ht_set.target_plates, list(ht_set.band_config), inv)
    assert assembled.prospective_ht_setups[ht_mv.id] == expected_next
    assert assembled.prospective_ht_setups[ht_mv.id] != (
        ht_set.target_plates, ht_set.band_config,
    ), "prospective must be the NEXT setup, strictly advanced past prescribed current"


def test_uncalibrated_ht_does_not_fabricate_plates(gen_db):
    """A fully uncalibrated HT movement (no current_load, no ht_plates) must be
    assembled needs-calibration-style: no target_plates/band_config/target_felt_peak
    fabricated, and no entry recorded in prospective_ht_setups. Mirrors the
    non-HT needs-calibration path's "never fabricate a floor" guarantee."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D6 Weak Points", gen_db)
    ctx = resolve_context("D6 Weak Points", sk, gen_db, wk)
    sel = program_selections(sk)

    assembled = assemble(sel, sk, ctx, gen_db)

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    ht_sets = _planned_sets_for_movement(assembled, ht_mv.id)
    assert ht_sets, "expected the HT movement to be assembled into at least one set"
    for ps in ht_sets:
        assert ps.target_plates is None
        assert not ps.band_config
        assert ps.target_felt_peak is None

    assert ht_mv.id not in assembled.prospective_ht_setups


def test_assemble_does_not_write_ht_setup(gen_db_calibrated):
    """Mirrors the existing current_load no-write gate: assemble() must NOT
    write ht_plates/ht_band_config — only commit_session may (Option-C)."""
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D6 Weak Points", gen_db)
    ctx = resolve_context("D6 Weak Points", sk, gen_db, wk)
    sel = program_selections(sk)

    before = {s.movement_id: (s.ht_plates, s.ht_band_config)
              for s in gen_db.exec(select(MovementState)).all()}
    assemble(sel, sk, ctx, gen_db)
    after = {s.movement_id: (s.ht_plates, s.ht_band_config)
             for s in gen_db.exec(select(MovementState)).all()}
    assert before == after, "assemble must NOT write ht_plates/ht_band_config"


# ---------------------------------------------------------------------------
# 2. commit_session persists the HT setup (Option-C sole writer)
# ---------------------------------------------------------------------------

def test_commit_persists_ht_setup(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D6 Weak Points", gen_db)
    ctx = resolve_context("D6 Weak Points", sk, gen_db, wk)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    expected_plates, expected_config = assembled.prospective_ht_setups[ht_mv.id]

    commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    st = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
    ).one()
    assert st.ht_plates == expected_plates
    assert st.ht_band_config == expected_config


# ---------------------------------------------------------------------------
# 3. _rule_driven at-cap guard for COMPOSITE / HIP_THRUST movements
# ---------------------------------------------------------------------------

def test_rule_driven_composite_ht_at_cap_is_noop():
    mv = Movement(
        name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust",
        lift_category=LiftCategory.HIP_THRUST,
        progression_mode=ProgressionMode.COMPOSITE,
        increment_ladder=[5], cap=220, rep_ladder=[8, 10, 12],
    )
    st = MovementState(movement_id=1, day_id="d2", current_load=220,
                       current_increment_tier=0, consecutive_advance_count=2)
    r = advance(
        ProgressionRule.RULE_DRIVEN, st,
        SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                   session_performed=True),
        mv, 1,
    )
    assert r.advanced is False
    assert r.active_rule == ProgressionRule.RULE_DRIVEN.value
    assert r.new_rep_target is None, "must NOT hand off to REP_LADDER"
    assert r.consecutive_advance_count == 2, "streak must be preserved, not reset"


def test_rule_driven_composite_ht_below_cap_is_noop():
    # Below cap, COMPOSITE/HIP_THRUST movements must still no-op — the assembler
    # (ht_next_setup), not this rule's tier-advance path, drives HT progression
    # regardless of cap. One progression path, not two.
    mv = Movement(
        name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust",
        lift_category=LiftCategory.HIP_THRUST,
        progression_mode=ProgressionMode.COMPOSITE,
        increment_ladder=[5], cap=220, rep_ladder=[8, 10, 12],
    )
    st = MovementState(movement_id=1, day_id="d2", current_load=100,
                       current_increment_tier=0, consecutive_advance_count=2)
    r = advance(
        ProgressionRule.RULE_DRIVEN, st,
        SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                   session_performed=True),
        mv, 1,
    )
    assert r.advanced is False
    assert r.active_rule == ProgressionRule.RULE_DRIVEN.value
    assert r.new_tier is None, "must NOT bump the tier"
    assert r.consecutive_advance_count == st.consecutive_advance_count == 2, \
        "streak must be preserved, not reset"


def test_rule_driven_non_composite_at_cap_still_hands_to_rep_ladder():
    # Explicit boundary check: a movement that is neither COMPOSITE nor
    # HIP_THRUST keeps the pre-existing at-cap -> REP_LADDER handoff.
    mv = Movement(
        name="Some Ladder Movement", base_name="Some Ladder Movement",
        lift_category=LiftCategory.NONE, progression_mode=ProgressionMode.LADDER,
        increment_ladder=[5], cap=220, rep_ladder=[8, 10, 12],
    )
    st = MovementState(movement_id=2, day_id="d2", current_load=220,
                       current_increment_tier=0)
    r = advance(
        ProgressionRule.RULE_DRIVEN, st,
        SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                   session_performed=True),
        mv, 1,
    )
    assert r.active_rule == ProgressionRule.REP_LADDER.value


# ---------------------------------------------------------------------------
# 4. wear-gate: a retired BandPair must never be prescribed (Task 1)
# ---------------------------------------------------------------------------

def test_assembler_does_not_prescribe_a_retired_band(gen_db_calibrated):
    # NOTE: this test needs a PLAIN (non-derived, non-unified) HT slot that
    # independently wear-gates against a retired band -- D6's real d6_g1c is
    # derived and intentionally skips that gating (see module docstring), and
    # there is no other real Hip Thrust TierExercise left in the program
    # after this task, so a synthetic slot on D5's real ProgramDay is used
    # (mirrors test_ht_unification.py's _synthetic_ht_slot pattern).
    #
    # To exercise the wear-gate NON-VACUOUSLY we force a NOT-at-cap band
    # setup: 180 plates + Orange (bottom 198, peak 225). Ungated, the
    # raise-plates shortcut WOULD fire (185+Orange, bottom 203 <= 220) and
    # KEEP Orange in the config -- so retiring Orange genuinely distinguishes
    # gated from ungated behavior (an at-cap 202 setup would exclude Orange
    # by pure arithmetic regardless of `usable`, making the test vacuous).
    gen_db = gen_db_calibrated
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    orange = gen_db.exec(
        select(BandPair).where(BandPair.label == "#0 Orange")
    ).one()

    _synthetic_plain_ht_slot(gen_db, "D5 Lower B", ht_mv.id, "test_wear_gate_d5_ht")

    st = MovementState(
        movement_id=ht_mv.id, day_id="D5 Lower B",
        ht_plates=180.0, ht_band_config=[orange.id],
    )
    gen_db.add(st)
    gen_db.commit()

    # Retire Orange -- the band the HT movement is currently using.
    orange.usable = False
    gen_db.add(orange)
    gen_db.commit()

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    _stage_clean_ht_advance(
        gen_db, ht_mv.id, "D5 Lower B", 180.0, [orange.id], wk,
    )

    sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", sk, gen_db, wk)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    ht_set = _first_ht_working_set(assembled)
    assert ht_set.band_config is not None
    # Prescribe-current (2026-07-06): the planned set shows the athlete's CURRENT
    # setup for THIS session, which still names Orange (retiring a band mid-cycle
    # does not rewrite what the athlete is already set up with). The wear-gate
    # guards ADVANCEMENT — ht_next_setup, which now produces the prospective/next
    # setup staged for commit — so a retired band must never appear in the NEXT
    # setup: ungated, the raise-plates shortcut WOULD keep Orange (185+Orange,
    # bottom 203 <= 225); gated, it reconfigures off the retired band.
    next_plates, next_config = assembled.prospective_ht_setups[ht_mv.id]
    assert orange.id not in next_config     # retired Orange is never prescribed for the next setup
