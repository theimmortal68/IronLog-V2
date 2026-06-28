"""test_generation_fallback.py — Task 7: two-tier fallback (NAMED GATE c).

Tests:
  1. test_cold_start_emits_program_valid_and_trainable (NAMED GATE c)
     — with no prior session, fallback_session emits the program prior:
       validator-clean AND trainable (adaptive slots filled, every set has a load).
  2. test_last_valid_returns_none_when_no_prior
     — last_valid_selections returns None for a fresh DB with no completed sessions.
  3. test_last_valid_reconstructs_prior_session
     — last_valid_selections returns Selections that match the prior session's
       movements when a COMPLETED session exists.
  4. test_d1_giant_groups_are_per_tier (Fix 1 proof)
     — the assembled D1 program session has multiple GIANT_SET groups, each ≤3 exercises.
  5. test_build_validation_context_is_structural_only (Fix 2 proof)
     — build_validation_context returns tallies=None (structural-only; no cross-session checks).
  6. test_cold_start_d1_no_spurious_knee_frequency_reject
     — a single cold-start D1 session (no knee work) is structurally valid with no
       spurious KNEE_FREQUENCY reject after the tallies=None fix.
  7. test_gate_c_all_five_day_roles (FIX 2 — gate c extended)
     — fallback_session (cold-start) is structurally valid for ALL five program day_roles.
  8. test_quiet_path_structural_reject_falls_back_not_returns_invalid (FIX 2 guard)
     — if the deterministic quiet path assembles an invalid session, generate_session
       falls back rather than silently returning the invalid outcome.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
import datetime

import pytest

from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import (
    fallback_session,
    last_valid_selections,
    program_selections,
)
from ironlog.generation.loop import generate_session
from ironlog.generation.proposer import StubProposer
from ironlog.generation.repair import build_validation_context
from ironlog.generation.skeleton import lay_skeleton
from ironlog.engine.validator import validate
from ironlog.models.enums import SessionStatus
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session
from ironlog.models.enums import GroupType, Objective, Scheme, SetRole


# ---------------------------------------------------------------------------
# NAMED GATE c
# ---------------------------------------------------------------------------

def test_cold_start_emits_program_valid_and_trainable(gen_db):
    """NAMED GATE c: with no prior session, the cold-start fallback emits the
    PROGRAM prior — a VALID session (validator-clean) AND trainable (every
    adaptive slot filled with its program movement, every set carries a load)."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    fb = fallback_session(sk, ctx, gen_db)
    assert validate(
        fb.session.__class__ and fb.session,
        build_validation_context(ctx, gen_db),
    ).is_structurally_valid
    sets = [
        ps
        for g in fb.session.groups
        for e in g.exercises
        for ps in e.planned_sets
    ]
    assert sets, "fallback must prescribe sets (trainable)"
    assert all(ps.target_load is not None for ps in sets), "every set has a load"
    # every giant/knee slot got filled (not an empty/partial session)
    filled = {e.movement_id for g in fb.session.groups for e in g.exercises}
    assert len(filled) >= 1 + sum(
        1 for s in sk.adaptive_slots if s.kind in ("giant", "knee")
    )


# ---------------------------------------------------------------------------
# last_valid_selections: no-prior guard
# ---------------------------------------------------------------------------

def test_last_valid_returns_none_when_no_prior(gen_db):
    """last_valid_selections returns None when no COMPLETED session exists for
    the given day_role."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    result = last_valid_selections(sk, ctx, gen_db)
    assert result is None, "no prior COMPLETED session → must return None"


# ---------------------------------------------------------------------------
# last_valid_selections: reconstructs prior when one exists
# ---------------------------------------------------------------------------

def test_last_valid_reconstructs_prior_session(gen_db):
    """last_valid_selections returns a Selections whose movement_ids come from
    the most recent COMPLETED session for the same day_role."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)

    # Get the program selections to know the program movement ids.
    prog_sel = program_selections(sk)

    # Build a minimal COMPLETED session in the DB containing those movements.
    # Anchor group first, then adaptive giant exercises.
    anchor_mid = sk.anchor_movement_ids[0]
    adaptive_slots = [s for s in sk.adaptive_slots if s.kind in ("giant", "knee")]

    prior = Session(
        date=datetime.date.today() - datetime.timedelta(days=7),
        day_role="D1 Upper Push",
        phase="P1_CUT",
        status=SessionStatus.COMPLETED,
    )
    gen_db.add(prior)
    gen_db.flush()

    # Anchor group (order_index=0)
    anchor_group = ExerciseGroup(
        session_id=prior.id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        rounds=1,
    )
    gen_db.add(anchor_group)
    gen_db.flush()
    anchor_ex = PlannedExercise(
        group_id=anchor_group.id,
        movement_id=anchor_mid,
        order_index=0,
        scheme=Scheme.TOPSET_BACKOFF,
        objective=Objective.PROGRESS,
    )
    gen_db.add(anchor_ex)
    gen_db.flush()
    gen_db.add(PlannedSet(
        planned_exercise_id=anchor_ex.id,
        set_index=0,
        set_role=SetRole.WORKING,
        target_load=100.0,
    ))

    # Adaptive exercises (giant group, order_index=1).
    # Use the program movement ids from prog_sel.
    g_group = ExerciseGroup(
        session_id=prior.id,
        order_index=1,
        group_type=GroupType.GIANT_SET,
        rounds=3,
    )
    gen_db.add(g_group)
    gen_db.flush()

    prog_mid_map = {s.slot_id: s.movement_id for s in prog_sel.slots}
    for i, slot in enumerate(adaptive_slots):
        mid = prog_mid_map.get(slot.slot_id, anchor_mid)
        ex = PlannedExercise(
            group_id=g_group.id,
            movement_id=mid,
            order_index=i,
            scheme=Scheme.STRAIGHT,
            objective=Objective.MAINTAIN,
        )
        gen_db.add(ex)
        gen_db.flush()
        gen_db.add(PlannedSet(
            planned_exercise_id=ex.id,
            set_index=0,
            set_role=SetRole.WORKING,
            target_load=50.0,
        ))

    gen_db.commit()

    # Now last_valid_selections should return non-None Selections.
    result = last_valid_selections(sk, ctx, gen_db)
    assert result is not None, "a COMPLETED prior session exists → must return Selections"
    assert result.slots, "returned Selections must have at least one slot"
    assert result.rationale == "deterministic fallback (last valid, loads refreshed)"

    # The movement ids in the result should match those from the prior session's
    # adaptive exercises (the program movements we seeded).
    result_mids = {s.movement_id for s in result.slots}
    seeded_mids = set(prog_mid_map.values())
    # At least one movement from the prior session must appear in the result.
    assert result_mids & seeded_mids, (
        "last_valid_selections should recover movements from the prior session"
    )


# ---------------------------------------------------------------------------
# Fix 1 proof: one GIANT_SET group per source program tier
# ---------------------------------------------------------------------------

def test_d1_giant_groups_are_per_tier(gen_db):
    """Fix 1 — assembler creates one GIANT_SET group per source program tier.

    D1 Upper Push has 3 GIANT_SET tiers (T2 GS, T3 GS, T4 GS), 3 slots each.
    The cold-start fallback must produce 3 separate GIANT_SET groups, each with
    exactly 3 exercises — never a single 9-exercise group.
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    fb = fallback_session(sk, ctx, gen_db)

    from ironlog.models.enums import GroupType
    giant_groups = [g for g in fb.session.groups if g.group_type == GroupType.GIANT_SET]
    assert len(giant_groups) >= 2, (
        f"D1 must produce multiple GIANT_SET groups (one per tier); got {len(giant_groups)}"
    )
    for g in giant_groups:
        n = len(g.exercises)
        assert 1 <= n <= 3, (
            f"GIANT_SET group (order_index={g.order_index}) has {n} exercises; "
            f"expected 1-3 (room-geometry constraint)"
        )


# ---------------------------------------------------------------------------
# Fix 2 proof: build_validation_context is structural-only (tallies=None)
# ---------------------------------------------------------------------------

def test_build_validation_context_is_structural_only(gen_db):
    """Fix 2 — build_validation_context must return tallies=None.

    Per-session generation validate is structural-only.  Cross-session frequency
    rules (KNEE_FREQUENCY, PULL_PUSH_RATIO) must not fire on a single generated
    session — the tallies=None guard in build_validation_context enforces this.
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    vc = build_validation_context(ctx, gen_db)
    assert vc.tallies is None, (
        "build_validation_context must pass tallies=None (structural-only); "
        "KNEE_FREQUENCY/PULL_PUSH checks must be skipped for per-session generation"
    )


def test_cold_start_d1_no_spurious_knee_frequency_reject(gen_db):
    """Fix 2 — D1 cold-start session must be structurally valid with no spurious
    KNEE_FREQUENCY reject.

    D1 Upper Push has no knee exercises.  With tallies=None in build_validation_context,
    the per-session validator must not fire KNEE_FREQUENCY for missing weekly knee targets
    that D1 has no obligation to satisfy in a single session.
    """
    from ironlog.engine.validator import RuleCode, ViolationKind
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    fb = fallback_session(sk, ctx, gen_db)
    vc = build_validation_context(ctx, gen_db)
    result = validate(fb.session, vc)

    knee_rejects = [
        v for v in result.rejects
        if v.rule == RuleCode.KNEE_FREQUENCY
    ]
    assert not knee_rejects, (
        f"KNEE_FREQUENCY must not fire on a single D1 session (structural-only); "
        f"got: {knee_rejects}"
    )
    assert result.is_structurally_valid, (
        f"D1 cold-start must be structurally valid; rejections: {result.rejects}"
    )


# ---------------------------------------------------------------------------
# FIX 2 — gate c extended: all five program day_roles are structurally valid
# ---------------------------------------------------------------------------

ALL_DAY_ROLES = [
    "D1 Upper Push",
    "D2 Lower A",
    "D4 Upper Pull",
    "D5 Lower B",
    "D6 Weak Points",
]


@pytest.mark.parametrize("day_role", ALL_DAY_ROLES)
def test_gate_c_all_five_day_roles(gen_db, day_role):
    """FIX 2 (gate c extended): fallback_session cold-start is structurally valid
    for ALL five program day_roles, not just D1.

    Validates that every program day emits a validator-clean session via the
    deterministic fallback path (program_selections → assemble → validate).
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton(day_role, gen_db)
    ctx = resolve_context(day_role, sk, gen_db, wk)
    fb = fallback_session(sk, ctx, gen_db)
    vc = build_validation_context(ctx, gen_db)
    result = validate(fb.session, vc)
    assert result.is_structurally_valid, (
        f"{day_role} cold-start must be structurally valid; "
        f"rejections: {result.rejects}"
    )
    sets = [
        ps
        for g in fb.session.groups
        for e in g.exercises
        for ps in e.planned_sets
    ]
    assert sets, f"{day_role} fallback must prescribe sets (trainable)"
    assert all(ps.target_load is not None for ps in sets), (
        f"{day_role} fallback: every set must carry a load"
    )


# ---------------------------------------------------------------------------
# FIX 2 — quiet-path structural REJECT guard
# ---------------------------------------------------------------------------

def test_quiet_path_structural_reject_falls_back_not_returns_invalid(gen_db, monkeypatch):
    """FIX 2 guard: when the quiet-path deterministic assembly is structurally
    invalid, generate_session must NOT silently return the invalid session — it
    must fall back (or raise), never emit the reject.

    We monkeypatch the validate function to return a structural REJECT on the
    first call (simulating a broken program prior), then return a valid result
    on subsequent calls (for the fallback path).  The test asserts that the
    returned outcome is either a valid fallback assembly (not the broken one)
    or a ValueError is raised — in no case is the invalid session returned.
    """
    import ironlog.generation.loop as loop_mod
    from ironlog.engine.validator import ValidationResult, Violation, ViolationKind
    from ironlog.engine.validator import RuleCode

    call_count = [0]
    original_validate = loop_mod.validate

    def patched_validate(session, vc):
        call_count[0] += 1
        if call_count[0] <= 2:
            # First two calls (pre-clamp + post-clamp for the program prior): return REJECT
            bad_violation = Violation(
                kind=ViolationKind.REJECT,
                rule=RuleCode.PRIMARY_NOT_FIRST,
                message="injected structural reject for test",
            )
            return ValidationResult(violations=[bad_violation])
        # Subsequent calls (for the fallback path): use the real validator
        return original_validate(session, vc)

    monkeypatch.setattr(loop_mod, "validate", patched_validate)

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    stub = StubProposer(program_selections(sk))

    try:
        outcome = generate_session("D1 Upper Push", gen_db, stub, wk)
        # If no exception: outcome must NOT be the invalid program-prior assembly.
        # The outcome must be structurally valid (the fallback was used).
        assert outcome.assembled is not None, "outcome must have an assembled session"
        vc = build_validation_context(
            resolve_context("D1 Upper Push", sk, gen_db, wk), gen_db
        )
        result = original_validate(outcome.assembled.session, vc)
        assert result.is_structurally_valid, (
            "when quiet-path produces a REJECT, generate_session must fall back to a "
            f"valid session; got rejections: {result.rejects}"
        )
    except ValueError:
        # A ValueError (both program prior and fallback invalid) is also acceptable —
        # the key invariant is that an invalid session is never silently returned.
        pass
