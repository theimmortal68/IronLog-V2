"""tests/test_slot_override_skeleton.py — lay_skeleton honors SlotMovementOverride.

Precedence under test: active SlotMovementOverride > MesoRotation(meso_number)
> te.movement_id (the base program movement). Base program is never mutated —
only live-state rows (SlotMovementOverride, MesoRotation) change resolution.
NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import SQLModel, Session as DBSession, create_engine, select

from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import Movement
from ironlog.models.program import (
    MesoRotation, Program, ProgramDay, SlotMovementOverride, Tier, TierExercise, TierKind,
)
from ironlog.models.session import Note
import ironlog.models  # register tables


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _seed(db):
    """Program with a bench anchor slot (T1) + an unrelated adaptive slot (T2)."""
    prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
    db.add(prog); db.commit(); db.refresh(prog)
    day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
    db.add(day); db.commit(); db.refresh(day)

    t1 = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    db.add(t1); db.commit(); db.refresh(t1)
    t2 = Tier(program_day_id=day.id, tier_label="T2", tier_order=2, tier_kind=TierKind.ACCESSORY)
    db.add(t2); db.commit(); db.refresh(t2)

    bench = Movement(name="Bench Press [PB]", base_name="Bench Press")
    incline = Movement(name="Incline Bench [PB]", base_name="Incline Bench")
    overhead_press = Movement(name="Overhead Press [PB]", base_name="Overhead Press")
    close_grip = Movement(name="Close Grip Bench [PB]", base_name="Close Grip Bench")
    db.add(bench); db.add(incline); db.add(overhead_press); db.add(close_grip)
    db.commit()
    db.refresh(bench); db.refresh(incline); db.refresh(overhead_press); db.refresh(close_grip)

    bench_te = TierExercise(tier_id=t1.id, slot_id="d1_t1", movement_id=bench.id,
                             exercise_order=1, tier_role="anchor")
    db.add(bench_te); db.commit(); db.refresh(bench_te)

    other_te = TierExercise(tier_id=t2.id, slot_id="d1_t2a", movement_id=close_grip.id,
                             exercise_order=1, tier_role="semi")
    db.add(other_te); db.commit(); db.refresh(other_te)

    return {
        "bench": bench, "incline": incline, "overhead_press": overhead_press,
        "close_grip": close_grip, "bench_te": bench_te, "other_te": other_te,
    }


def test_skeleton_emits_base_movement_with_no_override():
    db = DBSession(_engine())
    ctx = _seed(db)
    sk = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk.anchor_movement_ids == [ctx["bench"].id]
    assert sk.adaptive_slots[0].program_movement_id == ctx["close_grip"].id


def test_active_override_swaps_only_its_slot():
    db = DBSession(_engine())
    ctx = _seed(db)
    note = Note(text="switch bench to incline")
    db.add(note); db.commit(); db.refresh(note)
    override = SlotMovementOverride(
        tier_exercise_id=ctx["bench_te"].id,
        override_movement_id=ctx["incline"].id,
        source_note_id=note.id,
    )
    db.add(override); db.commit(); db.refresh(override)

    sk = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk.anchor_movement_ids == [ctx["incline"].id], "overridden slot emits incline"
    assert sk.adaptive_slots[0].program_movement_id == ctx["close_grip"].id, \
        "other slot is unaffected by the override"

    # Base program row is untouched by the override.
    db.refresh(ctx["bench_te"])
    assert ctx["bench_te"].movement_id == ctx["bench"].id

    # active=False reverts to the base movement.
    override.active = False
    db.add(override); db.commit()
    sk_reverted = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk_reverted.anchor_movement_ids == [ctx["bench"].id]
    assert sk_reverted.adaptive_slots[0].program_movement_id == ctx["close_grip"].id


def test_adaptive_slot_meso_rotation_fires_through_skeleton(gen_db):
    """Regression lock: lay_skeleton now resolves MesoRotation for NON-anchor
    (adaptive) slots too, via _effective_movement_id. The seed attaches a meso-2
    rotation to D4's d4_t2a (Meadows Row [semi] -> Pendlay Row); prove it fires
    at meso_number=2 and does NOT fire at meso_number=1 (rotation is meso-gated,
    not always-on). Without this, a refactor could silently revert the adaptive
    branch to raw te.movement_id and stay green.
    """
    from ironlog.models.program import MesoRotation, TierExercise

    te = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d4_t2a")
    ).one()
    mr = gen_db.exec(
        select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.meso_number == 2,
        )
    ).one()
    assert mr.movement_id != te.movement_id, \
        "the seeded d4_t2a meso-2 rotation must be a real movement swap"

    m2 = lay_skeleton("D4 Upper Pull", gen_db, meso_number=2)
    slot2 = next(s for s in m2.adaptive_slots if s.slot_id == "d4_t2a")
    assert slot2.program_movement_id == mr.movement_id, \
        "meso-2 adaptive slot resolves to the seeded MesoRotation target (Pendlay Row)"

    m1 = lay_skeleton("D4 Upper Pull", gen_db, meso_number=1)
    slot1 = next(s for s in m1.adaptive_slots if s.slot_id == "d4_t2a")
    assert slot1.program_movement_id == te.movement_id, \
        "meso-1 adaptive slot emits the base movement — rotation is meso-gated"


def test_override_takes_precedence_over_meso_rotation():
    db = DBSession(_engine())
    ctx = _seed(db)
    note = Note(text="switch bench to incline")
    db.add(note); db.commit(); db.refresh(note)

    # meso_number=2 rotates bench -> overhead press (no override yet).
    mr = MesoRotation(tier_exercise_id=ctx["bench_te"].id, meso_number=2,
                       movement_id=ctx["overhead_press"].id)
    db.add(mr); db.commit()
    sk_meso_only = lay_skeleton("D1 Upper Push", db, meso_number=2)
    assert sk_meso_only.anchor_movement_ids == [ctx["overhead_press"].id], \
        "meso rotation applies when no override is active"

    # An active override on the same slot wins over the meso rotation.
    override = SlotMovementOverride(
        tier_exercise_id=ctx["bench_te"].id,
        override_movement_id=ctx["incline"].id,
        source_note_id=note.id,
    )
    db.add(override); db.commit(); db.refresh(override)
    sk_override = lay_skeleton("D1 Upper Push", db, meso_number=2)
    assert sk_override.anchor_movement_ids == [ctx["incline"].id], \
        "active override beats meso rotation"
    assert sk_override.adaptive_slots[0].program_movement_id == ctx["close_grip"].id

    # Dismissing the override falls back to the meso rotation, not the base.
    override.active = False
    db.add(override); db.commit()
    sk_after_dismiss = lay_skeleton("D1 Upper Push", db, meso_number=2)
    assert sk_after_dismiss.anchor_movement_ids == [ctx["overhead_press"].id], \
        "reverting the override falls back to meso rotation, base program untouched"

    # meso_number=1 (no rotation row for meso 1) reverts fully to the base movement.
    sk_meso1 = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk_meso1.anchor_movement_ids == [ctx["bench"].id]
