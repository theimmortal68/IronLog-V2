"""test_generation_repair.py — Task 6: validate + bounded repair loop.

Three named tests:
  1. test_rejection_reasons_are_outcome_only_never_a_remedy
     — reasons carry the unmet requirement/status/locus; NO remedy words.
  2. test_apply_clamps_writes_corrected_value
     — CLAMP violation writes corrected_value to the right PlannedSet field.
  3. test_repair_exhausted_after_max_retries
     — loop runs exactly max_retries (3) times then returns exhausted=True.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
import datetime

from ironlog.engine.validator import (
    RuleCode, ValidationResult, Violation, ViolationKind,
)
from ironlog.generation.repair import apply_clamps, rejection_reasons


# ---------------------------------------------------------------------------
# 1. Outcome-only gate
# ---------------------------------------------------------------------------

def test_rejection_reasons_are_outcome_only_never_a_remedy():
    res = ValidationResult(violations=[Violation(
        kind=ViolationKind.REJECT, rule=RuleCode.KNEE_FREQUENCY,
        message="NORDIC frequency unmet: 0/2 (owed 2)")])
    reasons = rejection_reasons(res)
    assert reasons and "owed" in reasons[0].lower()
    joined = " ".join(reasons).lower()
    for remedy in ("add ", "swap ", "use ", "put "):
        assert remedy not in joined, "rejection must state the unmet fact, not a fix"


# ---------------------------------------------------------------------------
# 2. apply_clamps writes corrected_value to the correct field
# ---------------------------------------------------------------------------

def test_apply_clamps_writes_corrected_value():
    from ironlog.models.enums import GroupType, Objective, Scheme, SetRole
    from ironlog.models.session import (
        ExerciseGroup, PlannedExercise, PlannedSet, Session,
    )

    ps = PlannedSet(set_index=0, set_role=SetRole.WORKING, target_load=999.0)
    ex = PlannedExercise(movement_id=1, order_index=0,
                         scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN)
    ex.planned_sets.append(ps)
    g = ExerciseGroup(order_index=0, group_type=GroupType.STRAIGHT)
    g.exercises.append(ex)
    sess = Session(date=datetime.date.today(), day_role="D1 Upper Push", phase="CUT")
    sess.groups.append(g)

    res = ValidationResult(violations=[Violation(
        kind=ViolationKind.CLAMP, rule=RuleCode.LOAD_OVER_CAP, message="over cap",
        group_index=0, movement_id=1, set_index=0, corrected_value=100.0)])
    n = apply_clamps(sess, res)
    assert n == 1 and ps.target_load == 100.0


def test_apply_clamps_rpe_over_cap():
    """RPE_OVER_CAP writes corrected_value to target_rpe (not target_load)."""
    from ironlog.models.enums import GroupType, Objective, Scheme, SetRole
    from ironlog.models.session import (
        ExerciseGroup, PlannedExercise, PlannedSet, Session,
    )

    ps = PlannedSet(set_index=0, set_role=SetRole.WORKING, target_load=100.0,
                    target_rpe=10.0)
    ex = PlannedExercise(movement_id=2, order_index=0,
                         scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN)
    ex.planned_sets.append(ps)
    g = ExerciseGroup(order_index=0, group_type=GroupType.STRAIGHT)
    g.exercises.append(ex)
    sess = Session(date=datetime.date.today(), day_role="test", phase="CUT")
    sess.groups.append(g)

    res = ValidationResult(violations=[Violation(
        kind=ViolationKind.CLAMP, rule=RuleCode.RPE_OVER_CAP, message="rpe over cap",
        group_index=0, movement_id=2, set_index=0, corrected_value=8.0)])
    n = apply_clamps(sess, res)
    assert n == 1
    assert ps.target_rpe == 8.0
    assert ps.target_load == 100.0  # untouched


def test_apply_clamps_returns_zero_on_no_clamps():
    from ironlog.models.enums import GroupType, Objective, Scheme, SetRole
    from ironlog.models.session import (
        ExerciseGroup, PlannedExercise, PlannedSet, Session,
    )

    ps = PlannedSet(set_index=0, set_role=SetRole.WORKING, target_load=80.0)
    ex = PlannedExercise(movement_id=1, order_index=0,
                         scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN)
    ex.planned_sets.append(ps)
    g = ExerciseGroup(order_index=0, group_type=GroupType.STRAIGHT)
    g.exercises.append(ex)
    sess = Session(date=datetime.date.today(), day_role="test", phase="CUT")
    sess.groups.append(g)

    # Only a REJECT, no CLAMPs
    res = ValidationResult(violations=[Violation(
        kind=ViolationKind.REJECT, rule=RuleCode.KNEE_FREQUENCY,
        message="freq unmet")])
    n = apply_clamps(sess, res)
    assert n == 0


# ---------------------------------------------------------------------------
# 3. Bounded repair — exhausts at max_retries then returns exhausted=True
# ---------------------------------------------------------------------------

def test_repair_exhausted_after_max_retries(gen_db):
    """With a selection that always violates PRIMARY_NOT_FIRST (primary movement
    placed inside a GIANT_SET group), repair must exhaust at max_retries=3,
    return exhausted=True, assembled=None, attempts=3.

    Note: per-session validate is now STRUCTURAL-ONLY (tallies=None), so
    KNEE_FREQUENCY no longer fires here.  PRIMARY_NOT_FIRST is a structural
    REJECT that fires unconditionally when a primary movement lands in a
    GIANT_SET group.  The StubProposer always returns the same selection, so
    every attempt produces the same violation.
    """
    from ironlog.generation.context import resolve_context
    from ironlog.generation.proposer import Selections, SlotSelection, StubProposer
    from ironlog.generation.repair import propose_validate_repair
    from ironlog.generation.skeleton import lay_skeleton

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)

    # Use the anchor (primary) movement for a giant adaptive slot.
    # The assembler will place it into a GIANT_SET group → PRIMARY_NOT_FIRST REJECT.
    # The StubProposer always returns this same selection → always invalid → exhausts.
    anchor_mid = sk.anchor_movement_ids[0]  # Bench Press — is_primary=True
    first_giant = next(s for s in sk.adaptive_slots if s.kind == "giant")
    slot_sel = SlotSelection(first_giant.slot_id, anchor_mid)
    sel = Selections(ordering=[first_giant.slot_id], slots=[slot_sel], rationale="test")
    proposer = StubProposer(sel)

    outcome = propose_validate_repair(
        proposer, {}, sk, ctx, gen_db, max_retries=3
    )

    assert outcome.exhausted, "must exhaust after 3 retries"
    assert outcome.attempts == 3
    assert outcome.assembled is None
    assert outcome.rejections, "must carry outcome-only rejection reasons"
    # Verify the reasons are outcome-only (no remedy words)
    joined = " ".join(outcome.rejections).lower()
    for remedy in ("add ", "swap ", "use ", "put "):
        assert remedy not in joined, f"repair must not inject remedy: {remedy!r}"
