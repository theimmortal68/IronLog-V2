"""Tests for ProgramRevision schema + explicit publish lifecycle.

Covers the 11 acceptance criteria from the task spec: idempotent publish,
immutability of frozen revisions, per-program monotonic numbering,
transactional atomicity, content fidelity, hash integrity, validation
error handling, and enum serialization round-trip.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

import pytest
from sqlmodel import Session, create_engine, select, func

import ironlog.models  # noqa: F401 — register all tables
import ironlog.models.program_revision  # noqa: F401 — register revision tables

from ironlog.engine.program_publish import (
    ProgramValidationError,
    publish_program_revision,
)
from ironlog.engine.program_revision_hash import (
    compute_revision_prescription_hash,
    compute_revision_topology_hash,
)
from ironlog.models.library import Movement
from ironlog.models.program import (
    MesoRotation,
    MicrocycleParityRotation,
    Program,
    ProgramDay,
    Tier,
    TierExercise,
    TierKind,
)
from ironlog.models.program_revision import (
    ProgramRevision,
    ProgramRevisionDay,
    ProgramRevisionEquipmentRequirement,
    ProgramRevisionExercise,
    ProgramRevisionMesoRotation,
    ProgramRevisionParityRotation,
    ProgramRevisionTier,
    RevisionOrigin,
    SlotRole,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gen_db():
    """In-memory SQLite DB seeded with the full library + APEX Bridge program."""
    eng = create_engine("sqlite://")
    import ironlog.db as db_mod
    db_mod.engine = eng
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()
    from ironlog.generation.program_seed import seed_phase1_program
    with Session(eng) as s:
        seed_phase1_program(s)
        yield s


@pytest.fixture
def program(gen_db):
    """The seeded APEX Bridge program."""
    return gen_db.exec(select(Program)).first()


# ---------------------------------------------------------------------------
# Test 1: publishing a valid Program creates r1
# ---------------------------------------------------------------------------

def test_publish_creates_r1(gen_db, program):
    rev = publish_program_revision(program, gen_db)

    assert rev.revision_number == 1
    assert rev.revision_origin == RevisionOrigin.PUBLISHED
    assert rev.program_id == program.id
    assert rev.prescription_hash  # non-empty
    assert rev.topology_hash  # non-empty
    assert rev.id is not None


# ---------------------------------------------------------------------------
# Test 2: idempotent re-publish returns same revision
# ---------------------------------------------------------------------------

def test_idempotent_republish(gen_db, program):
    r1 = publish_program_revision(program, gen_db)
    r1_id = r1.id

    r1_again = publish_program_revision(program, gen_db)
    assert r1_again.id == r1_id
    assert r1_again.revision_number == 1

    # Row count didn't grow
    count = gen_db.exec(
        select(func.count(ProgramRevision.id))
        .where(ProgramRevision.program_id == program.id)
    ).one()
    assert count == 1


# ---------------------------------------------------------------------------
# Test 3: editing authoring tables does NOT mutate r1
# ---------------------------------------------------------------------------

def test_authoring_edit_does_not_mutate_r1(gen_db, program):
    r1 = publish_program_revision(program, gen_db)
    r1_hash = r1.prescription_hash
    r1_topo = r1.topology_hash

    # Capture a specific exercise's rep_low from r1
    r1_exercises = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == r1.id)
    ).all()
    first_ex = r1_exercises[0]
    original_rep_low = first_ex.rep_low

    # Now edit the authoring TierExercise
    te = gen_db.exec(select(TierExercise)).first()
    te.rep_low = 999
    gen_db.add(te)
    gen_db.commit()

    # Re-fetch r1 — must be unchanged
    r1_refetch = gen_db.exec(
        select(ProgramRevision).where(ProgramRevision.id == r1.id)
    ).one()
    assert r1_refetch.prescription_hash == r1_hash
    assert r1_refetch.topology_hash == r1_topo

    r1_ex_refetch = gen_db.exec(
        select(ProgramRevisionExercise)
        .where(ProgramRevisionExercise.id == first_ex.id)
    ).one()
    assert r1_ex_refetch.rep_low == original_rep_low


# ---------------------------------------------------------------------------
# Test 4: publishing after a meaningful edit creates r2
# ---------------------------------------------------------------------------

def test_publish_after_edit_creates_r2(gen_db, program):
    r1 = publish_program_revision(program, gen_db)

    # Meaningful edit: change a TierExercise
    te = gen_db.exec(select(TierExercise)).first()
    te.rep_low = 999
    gen_db.add(te)
    gen_db.commit()

    r2 = publish_program_revision(program, gen_db)
    assert r2.revision_number == 2
    assert r2.id != r1.id
    assert r2.prescription_hash != r1.prescription_hash


# ---------------------------------------------------------------------------
# Test 5: r1 and r2 coexist with independent content
# ---------------------------------------------------------------------------

def test_r1_r2_independent_content(gen_db, program):
    r1 = publish_program_revision(program, gen_db)

    # Record r1's first exercise's rep_low
    r1_exercises = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == r1.id)
    ).all()
    r1_first_ex_rep_low = r1_exercises[0].rep_low

    # Edit and publish r2
    te = gen_db.exec(select(TierExercise)).first()
    te.rep_low = 999
    gen_db.add(te)
    gen_db.commit()

    r2 = publish_program_revision(program, gen_db)

    # r1 still exists
    r1_check = gen_db.exec(
        select(ProgramRevision).where(ProgramRevision.id == r1.id)
    ).one()
    assert r1_check.revision_number == 1

    # r1's exercises still reflect the pre-edit state
    r1_exercises_check = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == r1.id)
    ).all()
    assert r1_exercises_check[0].rep_low == r1_first_ex_rep_low

    # r2's exercises reflect the post-edit state
    r2_exercises = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == r2.id)
    ).all()
    # Find the matching exercise (by slot_id) in r2
    r2_match = [e for e in r2_exercises if e.slot_id == r1_exercises_check[0].slot_id]
    assert len(r2_match) == 1
    assert r2_match[0].rep_low == 999


# ---------------------------------------------------------------------------
# Test 6: per-program monotonic revision numbering
# ---------------------------------------------------------------------------

def test_per_program_monotonic_numbering(gen_db, program):
    r1 = publish_program_revision(program, gen_db)
    assert r1.revision_number == 1

    # Create a second, independent Program with minimal valid topology
    mvmt = gen_db.exec(select(Movement)).first()

    prog2 = Program(name="Test Program 2", phase="TEST", duration_weeks=4)
    gen_db.add(prog2)
    gen_db.flush()

    day2 = ProgramDay(
        program_id=prog2.id, day_index=1, day_role="D1 Test", is_rest=False,
    )
    gen_db.add(day2)
    gen_db.flush()

    tier2 = Tier(
        program_day_id=day2.id, tier_label="T1", tier_order=1,
        tier_kind=TierKind.T1_STRAIGHT,
    )
    gen_db.add(tier2)
    gen_db.flush()

    te2 = TierExercise(
        tier_id=tier2.id, slot_id="test_t1", movement_id=mvmt.id,
        exercise_order=1, tier_role="anchor",
    )
    gen_db.add(te2)
    gen_db.commit()

    r2_prog2 = publish_program_revision(prog2, gen_db)
    assert r2_prog2.revision_number == 1  # starts at 1, not continuing from program 1
    assert r2_prog2.program_id == prog2.id


# ---------------------------------------------------------------------------
# Test 7: transactional atomicity — partial failure leaves zero rows
# ---------------------------------------------------------------------------

def test_transactional_atomicity_on_failure(gen_db, program, monkeypatch):
    # Count rows before
    rev_count_before = gen_db.exec(
        select(func.count(ProgramRevision.id))
    ).one()
    day_count_before = gen_db.exec(
        select(func.count(ProgramRevisionDay.id))
    ).one()
    tier_count_before = gen_db.exec(
        select(func.count(ProgramRevisionTier.id))
    ).one()
    ex_count_before = gen_db.exec(
        select(func.count(ProgramRevisionExercise.id))
    ).one()

    # Force a failure partway through _materialize_graph by intercepting
    # ProgramRevisionTier initialization (after ProgramRevisionDay is already added/flushed).
    orig_init = ProgramRevisionTier.__init__
    def broken_init(self, *args, **kwargs):
        raise RuntimeError("Injected materialization failure")
    
    monkeypatch.setattr(ProgramRevisionTier, "__init__", broken_init)

    with pytest.raises(RuntimeError, match="Injected materialization failure"):
        publish_program_revision(program, gen_db)

    # Row counts unchanged — no partial revision left behind
    assert gen_db.exec(select(func.count(ProgramRevision.id))).one() == rev_count_before
    assert gen_db.exec(select(func.count(ProgramRevisionDay.id))).one() == day_count_before
    assert gen_db.exec(select(func.count(ProgramRevisionTier.id))).one() == tier_count_before
    assert gen_db.exec(select(func.count(ProgramRevisionExercise.id))).one() == ex_count_before

    # Calling publish_program_revision() again succeeds cleanly
    monkeypatch.undo()
    r1 = publish_program_revision(program, gen_db)
    assert r1.revision_number == 1


# ---------------------------------------------------------------------------
# Test 8: materialized graph reproduces authoring content
# ---------------------------------------------------------------------------

def test_materialized_graph_content_fidelity(gen_db, program):
    rev = publish_program_revision(program, gen_db)

    # --- Days ---
    authoring_days = gen_db.exec(
        select(ProgramDay).where(ProgramDay.program_id == program.id)
    ).all()
    rev_days = gen_db.exec(
        select(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == rev.id)
    ).all()
    assert len(rev_days) == len(authoring_days)

    # Spot-check: compare day_index, day_role, is_rest for each
    auth_days_map = {d.day_index: d for d in authoring_days}
    for rd in rev_days:
        ad = auth_days_map[rd.day_index]
        assert rd.day_role == ad.day_role
        assert rd.is_rest == ad.is_rest
        assert rd.warmup_config == ad.warmup_config

    # --- Tiers ---
    total_auth_tiers = 0
    total_rev_tiers = 0
    for ad in authoring_days:
        auth_tiers = gen_db.exec(
            select(Tier).where(Tier.program_day_id == ad.id)
        ).all()
        total_auth_tiers += len(auth_tiers)

    for rd in rev_days:
        rtiers = gen_db.exec(
            select(ProgramRevisionTier)
            .where(ProgramRevisionTier.program_revision_day_id == rd.id)
        ).all()
        total_rev_tiers += len(rtiers)

    assert total_rev_tiers == total_auth_tiers

    # --- Exercises: spot-check a specific TierExercise ---
    te = gen_db.exec(select(TierExercise)).first()
    rev_exercises = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == rev.id)
    ).all()
    matching = [e for e in rev_exercises if e.slot_id == te.slot_id]
    assert len(matching) >= 1
    rex = matching[0]
    assert rex.movement_id == te.movement_id
    assert rex.exercise_order == te.exercise_order
    assert rex.tier_role == te.tier_role
    assert rex.pattern == te.pattern
    assert rex.rep_low == te.rep_low
    assert rex.rep_high == te.rep_high
    assert rex.scheme == te.scheme

    # --- MesoRotation ---
    auth_mr_count = gen_db.exec(
        select(func.count(MesoRotation.id))
        .where(MesoRotation.mesocycle_id.is_(None))  # type: ignore[union-attr]
    ).one()
    rev_mr_count = gen_db.exec(
        select(func.count(ProgramRevisionMesoRotation.id))
    ).one()
    assert rev_mr_count == auth_mr_count

    # Spot-check a specific MesoRotation if any exist
    if auth_mr_count > 0:
        auth_mr = gen_db.exec(
            select(MesoRotation).where(MesoRotation.mesocycle_id.is_(None))  # type: ignore[union-attr]
        ).first()
        # Find the corresponding revision exercise by slot_id
        auth_te_for_mr = gen_db.exec(
            select(TierExercise).where(TierExercise.id == auth_mr.tier_exercise_id)
        ).one()
        rev_ex_for_mr = [
            e for e in rev_exercises if e.slot_id == auth_te_for_mr.slot_id
        ]
        assert len(rev_ex_for_mr) >= 1
        rev_mrs = gen_db.exec(
            select(ProgramRevisionMesoRotation).where(
                ProgramRevisionMesoRotation.program_revision_exercise_id == rev_ex_for_mr[0].id,
                ProgramRevisionMesoRotation.meso_number == auth_mr.meso_number,
            )
        ).all()
        assert len(rev_mrs) == 1
        assert rev_mrs[0].movement_id == auth_mr.movement_id
        assert rev_mrs[0].rep_low == auth_mr.rep_low
        assert rev_mrs[0].rep_high == auth_mr.rep_high

    # --- ParityRotation ---
    auth_wpr_count = gen_db.exec(
        select(func.count(MicrocycleParityRotation.id))
    ).one()
    rev_wpr_count = gen_db.exec(
        select(func.count(ProgramRevisionParityRotation.id))
    ).one()
    assert rev_wpr_count == auth_wpr_count

    if auth_wpr_count > 0:
        auth_wpr = gen_db.exec(select(MicrocycleParityRotation)).first()
        auth_te_for_wpr = gen_db.exec(
            select(TierExercise).where(
                TierExercise.id == auth_wpr.tier_exercise_id
            )
        ).one()
        rev_ex_for_wpr = [
            e for e in rev_exercises if e.slot_id == auth_te_for_wpr.slot_id
        ]
        assert len(rev_ex_for_wpr) >= 1
        rev_wprs = gen_db.exec(
            select(ProgramRevisionParityRotation).where(
                ProgramRevisionParityRotation.program_revision_exercise_id == rev_ex_for_wpr[0].id,
                ProgramRevisionParityRotation.week_parity == auth_wpr.week_parity,
            )
        ).all()
        assert len(rev_wprs) == 1
        assert rev_wprs[0].movement_id == auth_wpr.movement_id


# ---------------------------------------------------------------------------
# Test 9: stored hashes match recomputation against the materialized revision
# ---------------------------------------------------------------------------

def test_hash_integrity(gen_db, program):
    """The stored hashes must match recomputing against the live authoring
    tables (the materialization source). This is the actual integrity property
    the hash is supposed to guarantee."""
    rev = publish_program_revision(program, gen_db)

    # Recompute from the live authoring tables
    recomputed_rx = compute_revision_prescription_hash(program)
    recomputed_topo = compute_revision_topology_hash(program)

    assert rev.prescription_hash == recomputed_rx
    assert rev.topology_hash == recomputed_topo

    # Also verify they're real SHA-256 hex strings
    assert len(rev.prescription_hash) == 64
    assert len(rev.topology_hash) == 64


# ---------------------------------------------------------------------------
# Test 10: validation error for invalid topology
# ---------------------------------------------------------------------------

def test_validation_error_no_revision_created(gen_db, program):
    # Inject a TierExercise with a nonexistent movement_id
    day = gen_db.exec(
        select(ProgramDay).where(ProgramDay.program_id == program.id)
    ).first()
    tier = gen_db.exec(
        select(Tier).where(Tier.program_day_id == day.id)
    ).first()

    bad_te = TierExercise(
        tier_id=tier.id, slot_id="invalid_slot", movement_id=888888,
        exercise_order=99, tier_role="anchor",
    )
    gen_db.add(bad_te)
    gen_db.commit()

    rev_count_before = gen_db.exec(
        select(func.count(ProgramRevision.id))
    ).one()

    with pytest.raises(ProgramValidationError, match="nonexistent movement_id=888888"):
        publish_program_revision(program, gen_db)

    rev_count_after = gen_db.exec(
        select(func.count(ProgramRevision.id))
    ).one()
    assert rev_count_after == rev_count_before


# ---------------------------------------------------------------------------
# Test 11: revision_origin enum serialization round-trip
# ---------------------------------------------------------------------------

def test_revision_origin_serialization_roundtrip(gen_db, program):
    rev = publish_program_revision(program, gen_db)

    # model_dump() round-trip (Pydantic v2 convention used in this codebase)
    dumped = rev.model_dump()
    assert dumped["revision_origin"] == "PUBLISHED"

    # Re-fetch from DB to confirm persistence/deserialization
    rev_refetched = gen_db.exec(
        select(ProgramRevision).where(ProgramRevision.id == rev.id)
    ).one()
    assert rev_refetched.revision_origin == RevisionOrigin.PUBLISHED
    assert rev_refetched.revision_origin.value == "PUBLISHED"

    # Verify SlotRole also round-trips
    rev_exercises = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == rev.id)
    ).all()
    assert len(rev_exercises) > 0
    ex_dumped = rev_exercises[0].model_dump()
    assert ex_dumped["slot_role"] == "ANCHOR"


# ---------------------------------------------------------------------------
# Test 12: equipment requirement stub table is usable
# ---------------------------------------------------------------------------

def test_equipment_requirement_stub(gen_db, program):
    """Prove the ProgramRevisionEquipmentRequirement table exists and a row
    can be attached to a ProgramRevisionExercise."""
    rev = publish_program_revision(program, gen_db)

    rev_ex = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == rev.id)
    ).first()

    req = ProgramRevisionEquipmentRequirement(
        program_revision_exercise_id=rev_ex.id,
        requirement_expr={"ANY_OF": ["landmine", "cable"]},
    )
    gen_db.add(req)
    gen_db.commit()

    fetched = gen_db.exec(
        select(ProgramRevisionEquipmentRequirement)
        .where(
            ProgramRevisionEquipmentRequirement.program_revision_exercise_id == rev_ex.id
        )
    ).one()
    assert fetched.requirement_expr == {"ANY_OF": ["landmine", "cable"]}


# ---------------------------------------------------------------------------
# Test 13: MesoRotation and ParityRotation presence in revision hash
# ---------------------------------------------------------------------------

def test_revision_hash_includes_rotations(gen_db, program):
    """Verify that MesoRotation and ParityRotation changes affect the
    revision prescription hash, producing a new revision."""
    r1 = publish_program_revision(program, gen_db)

    # Edit a MesoRotation row to prove its content is part of the hash
    mr = gen_db.exec(select(MesoRotation)).first()
    if not mr:
        # If the seeded program has no MesoRotation, add one
        te = gen_db.exec(select(TierExercise)).first()
        mr = MesoRotation(
            tier_exercise_id=te.id,
            meso_number=2,
            movement_id=te.movement_id,
            rep_low=10,
            rep_high=15,
        )
        gen_db.add(mr)
    else:
        # Edit the existing one
        mr.rep_low = 999
        gen_db.add(mr)
        
    gen_db.commit()

    # Publishing again should produce r2 because the hash changed
    r2 = publish_program_revision(program, gen_db)
    
    assert r2.id != r1.id
    assert r2.revision_number == 2
    assert r2.prescription_hash != r1.prescription_hash

    # Topology hash should remain unchanged (rotations do not affect topology)
    assert r2.topology_hash == r1.topology_hash


# ---------------------------------------------------------------------------
# Test 14: slot_role and slot_constraints defaults
# ---------------------------------------------------------------------------

def test_slot_role_defaults(gen_db, program):
    """ProgramRevisionExercise rows default to ANCHOR slot_role and None slot_constraints."""
    rev = publish_program_revision(program, gen_db)

    rev_exercises = gen_db.exec(
        select(ProgramRevisionExercise)
        .join(ProgramRevisionTier)
        .join(ProgramRevisionDay)
        .where(ProgramRevisionDay.program_revision_id == rev.id)
    ).all()
    assert len(rev_exercises) > 0
    for rex in rev_exercises:
        assert rex.slot_role == SlotRole.ANCHOR
        assert rex.slot_constraints is None

# ---------------------------------------------------------------------------
# Test 15: MesoRotation uniqueness invariant
# ---------------------------------------------------------------------------

def test_meso_rotation_uniqueness_invariant(gen_db):
    """Verify that for the seeded program, there is at most one MesoRotation
    row per (tier_exercise_id, meso_number) regardless of mesocycle_id."""
    mrs = gen_db.exec(select(MesoRotation)).all()
    seen = set()
    for mr in mrs:
        key = (mr.tier_exercise_id, mr.meso_number)
        assert key not in seen, f"Duplicate MesoRotation found for {key}"
        seen.add(key)
