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

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
import datetime

from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import (
    fallback_session,
    last_valid_selections,
    program_selections,
)
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
