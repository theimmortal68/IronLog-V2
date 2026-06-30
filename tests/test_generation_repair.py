"""test_generation_repair.py — Task 6: validate + bounded repair loop.

Named tests:
  1. test_rejection_reasons_are_outcome_only_never_a_remedy
     — reasons carry the unmet requirement/status/locus; NO remedy words.
  2. test_apply_clamps_writes_corrected_value
     — CLAMP violation writes corrected_value to the right PlannedSet field.
  3. test_repair_exhausted_after_max_retries
     — loop runs exactly max_retries (3) times then returns exhausted=True.
  4. test_check_menu_membership_flags_off_menu_and_passes_in_menu
     — off-menu movement_id produces an outcome-only MENU_MEMBERSHIP reason;
       in-menu movement_id returns []; slot absent from candidate_menus is skipped.
  5. test_repair_loop_rejects_off_menu_knee_selection
     — a proposer that always returns a non-knee movement for a knee slot
       exhausts without ever accepting the session (off-menu session NEVER commits).

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


# ---------------------------------------------------------------------------
# 4. check_menu_membership — unit test (no DB needed)
# ---------------------------------------------------------------------------

def test_check_menu_membership_flags_off_menu_and_passes_in_menu():
    """check_menu_membership must:
    - return an outcome-only MENU_MEMBERSHIP reason when movement_id is not in
      the slot's candidate menu,
    - return [] when the movement_id IS in the menu (in-menu passes),
    - skip slots that have no entry in candidate_menus (anchor/conditioning slots).

    Outcome-only contract: no remedy words ("add ", "swap ", "use ", "put ").
    """
    from ironlog.generation.proposer import Selections, SlotSelection
    from ironlog.generation.repair import check_menu_membership

    # Minimal duck-typed ctx — check_menu_membership only reads candidate_menus.
    class _StubCtx:
        candidate_menus = {"knee_slot_1": [10, 20, 30]}

    ctx = _StubCtx()

    # Off-menu: movement 99 is not in [10, 20, 30]
    sel_off = Selections(
        ordering=["knee_slot_1"],
        slots=[SlotSelection(slot_id="knee_slot_1", movement_id=99)],
        rationale="off-menu probe",
    )
    reasons = check_menu_membership(sel_off, ctx)
    assert reasons, "off-menu movement must produce a non-empty reason list"
    assert "MENU_MEMBERSHIP" in reasons[0], (
        f"reason must start with MENU_MEMBERSHIP; got: {reasons[0]!r}"
    )
    joined = " ".join(reasons).lower()
    for remedy in ("add ", "swap ", "use ", "put "):
        assert remedy not in joined, (
            f"menu-membership reason must state the fact, not a fix: {remedy!r} found"
        )

    # In-menu: movement 10 IS in [10, 20, 30]
    sel_in = Selections(
        ordering=["knee_slot_1"],
        slots=[SlotSelection(slot_id="knee_slot_1", movement_id=10)],
        rationale="in-menu probe",
    )
    assert check_menu_membership(sel_in, ctx) == [], (
        "in-menu movement must return an empty list"
    )

    # Slot absent from candidate_menus → skipped (no reason produced)
    sel_absent = Selections(
        ordering=["anchor_slot_x"],
        slots=[SlotSelection(slot_id="anchor_slot_x", movement_id=999)],
        rationale="absent-slot probe",
    )
    assert check_menu_membership(sel_absent, ctx) == [], (
        "slot not in candidate_menus must be skipped (no MENU_MEMBERSHIP reason)"
    )


# ---------------------------------------------------------------------------
# 5. Repair loop rejects off-menu knee selection — integration test
# ---------------------------------------------------------------------------

def test_repair_loop_rejects_off_menu_knee_selection(gen_db):
    """A proposer that always returns a non-knee movement for a knee slot must
    be rejected every attempt and exhaust to fallback — the off-menu session is
    NEVER assembled or committed.

    Also confirms (positive control) that check_menu_membership returns [] for
    a selection that IS in the knee slot's candidate menu.

    Uses D2 Lower A which has NORDIC, KOT, and TIB knee slots in the seeded DB.
    """
    from sqlmodel import select as sa_select

    from ironlog.generation.context import resolve_context
    from ironlog.generation.proposer import Selections, SlotSelection, StubProposer
    from ironlog.generation.repair import check_menu_membership, propose_validate_repair
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.models.library import Movement

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)

    # Pick the first knee slot (NORDIC — d2_t2a "Assisted Nordic")
    knee_slot = next(s for s in sk.adaptive_slots if s.kind == "knee")
    knee_menu = ctx.candidate_menus[knee_slot.slot_id]
    assert knee_menu, f"knee slot {knee_slot.slot_id!r} must have a non-empty menu"

    # Find any movement NOT in the knee menu to use as the off-menu selection.
    all_movements = gen_db.exec(sa_select(Movement)).all()
    off_menu_mid = next(
        m.id for m in all_movements if m.id not in knee_menu
    )

    # --- negative: off-menu selection must exhaust the loop ---
    off_menu_sel = Selections(
        ordering=[knee_slot.slot_id],
        slots=[SlotSelection(slot_id=knee_slot.slot_id, movement_id=off_menu_mid)],
        rationale="off-menu knee probe",
    )
    proposer = StubProposer(off_menu_sel)
    outcome = propose_validate_repair(proposer, {}, sk, ctx, gen_db, max_retries=3)

    assert outcome.exhausted, (
        "off-menu knee selection must never be accepted — loop must exhaust"
    )
    assert outcome.assembled is None, (
        "no session must be assembled when all attempts are off-menu"
    )
    assert outcome.attempts == 3
    assert any("MENU_MEMBERSHIP" in r for r in outcome.rejections), (
        f"rejections must contain a MENU_MEMBERSHIP reason; got: {outcome.rejections}"
    )

    # --- positive control: in-menu selection passes the membership check ---
    in_menu_sel = Selections(
        ordering=[knee_slot.slot_id],
        slots=[SlotSelection(slot_id=knee_slot.slot_id, movement_id=knee_menu[0])],
        rationale="in-menu control",
    )
    assert check_menu_membership(in_menu_sel, ctx) == [], (
        "in-menu knee movement must pass the membership check (return [])"
    )


# ---------------------------------------------------------------------------
# 6. Propose-failure robustness — a raising proposer exhausts, never propagates
# ---------------------------------------------------------------------------

def test_propose_failure_exhausts_not_propagates(gen_db):
    """A proposer whose .propose() raises ProposerError every attempt must be
    treated as a FAILED ATTEMPT each round — the loop exhausts and returns
    exhausted=True (assembled=None), NEVER propagating the exception.

    This is the load-bearing robustness guarantee: a live Gemini outage degrades
    to the deterministic fallback rather than surfacing as an unhandled 500.
    If the propose call were left unwrapped, this test would ERROR.
    """
    from ironlog.generation.context import resolve_context
    from ironlog.generation.gemini import ProposerError
    from ironlog.generation.repair import propose_validate_repair
    from ironlog.generation.skeleton import lay_skeleton

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)

    class _RaisingProposer:
        def propose(self, payload):
            raise ProposerError("simulated live proposer failure")

    outcome = propose_validate_repair(
        _RaisingProposer(), {}, sk, ctx, gen_db, max_retries=3
    )

    assert outcome.exhausted is True, "propose-failures must exhaust the loop"
    assert outcome.assembled is None, "no session assembled when every propose fails"
    assert outcome.attempts == 3, "every propose-failure counts as a failed attempt"
    assert any("PROPOSER_ERROR" in r for r in outcome.rejections), (
        f"rejections must carry an outcome-only PROPOSER_ERROR reason; "
        f"got: {outcome.rejections}"
    )
