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
     — the assembled D1 program session has multiple GIANT_SET groups, each matching
       its 3-exercise source tier.
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
from sqlmodel import select

from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import (
    fallback_session,
    last_valid_selections,
    program_selections,
)
from ironlog.generation.loop import generate_session
from ironlog.generation.proposer import Selections, SlotSelection, StubProposer
from ironlog.generation.repair import build_validation_context
from ironlog.generation.skeleton import lay_skeleton
from ironlog.engine.validator import validate
from ironlog.models.enums import SessionStatus
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session
from ironlog.models.enums import GroupType, Objective, Scheme, SetRole


# ---------------------------------------------------------------------------
# NAMED GATE c
# ---------------------------------------------------------------------------

def test_cold_start_emits_program_valid_and_trainable(gen_db_calibrated):
    """NAMED GATE c: with no prior SESSION, the fallback emits the PROGRAM prior —
    a VALID session (validator-clean) AND trainable.

    Reconciled for Task 3: "cold-start" here means no prior session (fallback path),
    NOT unconfigured loads.  Trainability requires real configured loads, so the
    gen_db_calibrated fixture seeds them (the wizard/calibration's job) — the floor
    fallback that used to fake trainability is gone.  Every LOADED movement's sets
    now carry a real load; bodyweight movements legitimately carry no load."""
    gen_db = gen_db_calibrated
    from ironlog.generation.load_trust import load_field_for_mode
    from ironlog.models.library import Movement
    from sqlmodel import select as _select

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    fb = fallback_session(sk, ctx, gen_db)
    assert validate(
        fb.session.__class__ and fb.session,
        build_validation_context(ctx, gen_db),
    ).is_structurally_valid
    movements = {m.id: m for m in gen_db.exec(_select(Movement)).all()}
    sets = [
        ps
        for g in fb.session.groups
        for e in g.exercises
        for ps in e.planned_sets
    ]
    assert sets, "fallback must prescribe sets (trainable)"
    # Every LOADED movement carries a real configured load (no floor fabrication);
    # bodyweight movements carry no external load (target_load None).
    for g in fb.session.groups:
        for e in g.exercises:
            m = movements[e.movement_id]
            needs_load = load_field_for_mode(m.progression_mode) is not None
            for ps in e.planned_sets:
                if needs_load:
                    assert ps.target_load is not None, (
                        f"configured loaded movement {m.name!r} must carry a load"
                    )
                else:
                    assert ps.target_load is None, (
                        f"bodyweight movement {m.name!r} must carry no external load"
                    )
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


def _set_slot_orders(db, slot_orders):
    from ironlog.models.program import TierExercise

    for slot_id, exercise_order in slot_orders.items():
        te = db.exec(
            select(TierExercise).where(TierExercise.slot_id == slot_id)
        ).one()
        te.exercise_order = exercise_order
        db.add(te)
    db.commit()


def _expected_last_valid(slots, movement_by_slot):
    ordered = [
        SlotSelection(slot_id=s.slot_id, movement_id=movement_by_slot[s.slot_id])
        for s in slots
    ]
    return Selections(
        ordering=[s.slot_id for s in ordered],
        slots=ordered,
        rationale="deterministic fallback (last valid, loads refreshed)",
    )


def _insert_completed_prior_session(db, day_role, anchor_mid, movement_ids):
    prior = Session(
        date=datetime.date(2026, 7, 1),
        day_role=day_role,
        phase="P1_CUT",
        status=SessionStatus.COMPLETED,
    )
    db.add(prior)
    db.flush()

    anchor_group = ExerciseGroup(
        session_id=prior.id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        rounds=1,
    )
    db.add(anchor_group)
    db.flush()
    anchor_ex = PlannedExercise(
        group_id=anchor_group.id,
        movement_id=anchor_mid,
        order_index=0,
        scheme=Scheme.TOPSET_BACKOFF,
        objective=Objective.PROGRESS,
    )
    db.add(anchor_ex)
    db.flush()
    db.add(PlannedSet(
        planned_exercise_id=anchor_ex.id,
        set_index=0,
        set_role=SetRole.WORKING,
        target_load=100.0,
    ))

    adaptive_group = ExerciseGroup(
        session_id=prior.id,
        order_index=1,
        group_type=GroupType.GIANT_SET,
        rounds=3,
    )
    db.add(adaptive_group)
    db.flush()
    for order_index, movement_id in enumerate(movement_ids):
        ex = PlannedExercise(
            group_id=adaptive_group.id,
            movement_id=movement_id,
            order_index=order_index,
            scheme=Scheme.STRAIGHT,
            objective=Objective.MAINTAIN,
        )
        db.add(ex)
        db.flush()
        db.add(PlannedSet(
            planned_exercise_id=ex.id,
            set_index=0,
            set_role=SetRole.WORKING,
            target_load=50.0,
        ))

    db.commit()


def test_last_valid_unchanged_order_is_byte_identical_to_positional_replay(gen_db):
    """No program-structure change: identity matching must emit exactly the same
    Selections as the old positional replay behavior."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D4 Upper Pull", gen_db)
    ctx = resolve_context("D4 Upper Pull", sk, gen_db, wk)
    adaptive_slots = [s for s in sk.adaptive_slots if s.kind in ("giant", "knee")]
    movement_by_slot = {s.slot_id: s.program_movement_id for s in adaptive_slots}

    _insert_completed_prior_session(
        gen_db,
        "D4 Upper Pull",
        sk.anchor_movement_ids[0],
        [movement_by_slot[s.slot_id] for s in adaptive_slots],
    )

    assert last_valid_selections(sk, ctx, gen_db) == _expected_last_valid(
        adaptive_slots,
        movement_by_slot,
    )


def test_last_valid_matches_reordered_prior_by_slot_identity(gen_db):
    """A completed session logged under the old D4 order must replay into the
    current skeleton by each slot's reachable movement identity, not by position.

    (2026-08-11, STAB maintenance-block redesign, Task 3: slot_ids updated from
    d4_t2a/d4_t2b/d4_t2c to d4_t2d/d4_t2e/d4_t2f -- D4's T2 GS was fully turned over
    per the FINAL doc, old slot_ids vacated. Generic reorder-replay logic under test
    here is unaffected by which movements occupy the slots.)
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    old_order = {"d4_t2d": 1, "d4_t2e": 2, "d4_t2f": 3}
    current_order = {"d4_t2d": 1, "d4_t2f": 2, "d4_t2e": 3}

    _set_slot_orders(gen_db, old_order)
    old_sk = lay_skeleton("D4 Upper Pull", gen_db)
    old_slots = [s for s in old_sk.adaptive_slots if s.kind in ("giant", "knee")]
    old_movement_by_slot = {s.slot_id: s.program_movement_id for s in old_slots}
    _insert_completed_prior_session(
        gen_db,
        "D4 Upper Pull",
        old_sk.anchor_movement_ids[0],
        [old_movement_by_slot[s.slot_id] for s in old_slots],
    )

    _set_slot_orders(gen_db, current_order)
    current_sk = lay_skeleton("D4 Upper Pull", gen_db)
    ctx = resolve_context("D4 Upper Pull", current_sk, gen_db, wk)
    current_slots = [
        s for s in current_sk.adaptive_slots if s.kind in ("giant", "knee")
    ]

    assert last_valid_selections(current_sk, ctx, gen_db) == _expected_last_valid(
        current_slots,
        old_movement_by_slot,
    )


def test_last_valid_matches_prior_meso_rotation_variant_after_reorder(gen_db):
    """A prior meso-2 swap remains attached to its owning slot after the current
    exercise order changes.

    (2026-08-11, STAB maintenance-block redesign, Task 3: repointed from D4's
    d4_t2a/b/c to D5's d5_t2a/b/c. D4's T2 GS was fully turned over per the FINAL doc
    and no longer carries any meso rotation; D5's d5_t2b was the program's
    adaptive-slot ("free" role) meso-rotation example, unaffected by that task.

    2026-08-12, Task 4: D5's own T2 GS is now ALSO fully turned over
    (d5_t2a/b/c no longer exist, replaced by d5_t2d/e/f) -- there is no real
    adaptive-role meso rotation left anywhere in the program. Repointed to
    d5_t2d/e/f with a synthetic, test-only MesoRotation inserted on d5_t2e
    (Nordic Curl Max [Ares]), mirroring the identical fix in
    test_generation_context.py / test_slot_override_skeleton.py.
    """
    from ironlog.models.library import Movement
    from ironlog.models.program import MesoRotation, TierExercise

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    d5_t2e = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d5_t2e")
    ).one()
    single_leg = gen_db.exec(
        select(Movement).where(Movement.base_name == "Reverse Hyper - Single Leg")
    ).one()
    gen_db.add(MesoRotation(tier_exercise_id=d5_t2e.id, meso_number=2, movement_id=single_leg.id))
    gen_db.commit()

    old_order = {"d5_t2d": 1, "d5_t2e": 2, "d5_t2f": 3}
    current_order = {"d5_t2f": 1, "d5_t2d": 2, "d5_t2e": 3}

    _set_slot_orders(gen_db, old_order)
    old_sk = lay_skeleton("D5 Lower B", gen_db, meso_number=2)
    old_slots = [s for s in old_sk.adaptive_slots if s.kind in ("giant", "knee")]
    old_movement_by_slot = {s.slot_id: s.program_movement_id for s in old_slots}
    _insert_completed_prior_session(
        gen_db,
        "D5 Lower B",
        old_sk.anchor_movement_ids[0],
        [old_movement_by_slot[s.slot_id] for s in old_slots],
    )

    _set_slot_orders(gen_db, current_order)
    current_sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", current_sk, gen_db, wk)
    current_slots = [
        s for s in current_sk.adaptive_slots if s.kind in ("giant", "knee")
    ]

    assert last_valid_selections(current_sk, ctx, gen_db) == _expected_last_valid(
        current_slots,
        old_movement_by_slot,
    )


def test_last_valid_retired_prior_rotation_falls_back_to_slot_program_movement(gen_db):
    """If a historical movement is no longer reachable for its slot, replay falls
    back to the current slot's program movement.

    (2026-08-11, STAB maintenance-block redesign, Task 3: repointed from D4's
    d4_t2a to D5's d5_t2b. D4's T2 GS was fully turned over per the FINAL doc and no
    longer carries any meso rotation; D5's d5_t2b was the program's adaptive-slot
    ("free" role) meso-rotation example, unaffected by that task.

    2026-08-12, Task 4: D5's own T2 GS is now ALSO fully turned over (d5_t2b
    no longer exists, replaced by d5_t2d/e/f) -- there is no real adaptive-
    role meso rotation left anywhere in the program. Repointed to d5_t2e
    with a synthetic, test-only MesoRotation inserted first (then deleted,
    as this test's own scenario requires), mirroring the identical fix in
    the reorder test above.
    """
    from ironlog.models.library import Movement
    from ironlog.models.program import MesoRotation, TierExercise

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    d5_t2e = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d5_t2e")
    ).one()
    single_leg = gen_db.exec(
        select(Movement).where(Movement.base_name == "Reverse Hyper - Single Leg")
    ).one()
    gen_db.add(MesoRotation(tier_exercise_id=d5_t2e.id, meso_number=2, movement_id=single_leg.id))
    gen_db.commit()

    old_sk = lay_skeleton("D5 Lower B", gen_db, meso_number=2)
    old_slots = [s for s in old_sk.adaptive_slots if s.kind in ("giant", "knee")]
    old_movement_by_slot = {s.slot_id: s.program_movement_id for s in old_slots}
    _insert_completed_prior_session(
        gen_db,
        "D5 Lower B",
        old_sk.anchor_movement_ids[0],
        [old_movement_by_slot[s.slot_id] for s in old_slots],
    )

    rotation = gen_db.exec(
        select(MesoRotation).where(MesoRotation.tier_exercise_id == d5_t2e.id)
    ).one()
    gen_db.delete(rotation)
    gen_db.commit()

    current_sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", current_sk, gen_db, wk)
    current_slots = [
        s for s in current_sk.adaptive_slots if s.kind in ("giant", "knee")
    ]
    current_movement_by_slot = {
        s.slot_id: old_movement_by_slot.get(s.slot_id, s.program_movement_id)
        for s in current_slots
    }
    current_movement_by_slot["d5_t2e"] = d5_t2e.movement_id

    assert last_valid_selections(current_sk, ctx, gen_db) == _expected_last_valid(
        current_slots,
        current_movement_by_slot,
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
        assert n == 3, (
            f"GIANT_SET group (order_index={g.order_index}) has {n} exercises; "
            "expected D1's 3-exercise source tier"
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
def test_gate_c_all_five_day_roles(gen_db_calibrated, day_role):
    """FIX 2 (gate c extended): fallback_session is structurally valid for ALL five
    program day_roles, not just D1.

    Reconciled for Task 3: loads are configured (gen_db_calibrated) so the program
    is genuinely trainable — every LOADED movement carries a real load (not a
    fabricated floor); bodyweight movements carry no external load.
    """
    gen_db = gen_db_calibrated
    from ironlog.generation.load_trust import load_field_for_mode
    from ironlog.models.library import Movement
    from sqlmodel import select as _select

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
    movements = {m.id: m for m in gen_db.exec(_select(Movement)).all()}
    sets = [
        ps
        for g in fb.session.groups
        for e in g.exercises
        for ps in e.planned_sets
    ]
    assert sets, f"{day_role} fallback must prescribe sets (trainable)"
    from ironlog.models.enums import LiftCategory, ProgressionMode

    for g in fb.session.groups:
        for e in g.exercises:
            m = movements[e.movement_id]
            needs_load = load_field_for_mode(m.progression_mode) is not None
            # HT (band-composite) movements are loaded via plates+bands, not the
            # scalar target_load (mirrors assembler._is_ht_movement) — clearing
            # target_load for these sets is the fix under test, not a regression.
            is_ht = (m.lift_category == LiftCategory.HIP_THRUST
                     or m.progression_mode == ProgressionMode.COMPOSITE)
            for ps in e.planned_sets:
                if is_ht:
                    assert ps.target_load is None, (
                        f"{day_role}: HT movement {m.name!r} must carry load via "
                        f"target_plates/band_config, not scalar target_load"
                    )
                    assert ps.target_plates is not None, (
                        f"{day_role}: HT movement {m.name!r} must carry a real "
                        f"target_plates value"
                    )
                elif needs_load:
                    assert ps.target_load is not None, (
                        f"{day_role}: loaded movement {m.name!r} must carry a load"
                    )
                else:
                    assert ps.target_load is None, (
                        f"{day_role}: bodyweight movement {m.name!r} carries no load"
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
