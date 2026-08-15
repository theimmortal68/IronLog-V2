"""tests/test_slot_override_skeleton.py — lay_skeleton honors SlotMovementOverride.

Precedence under test: active SlotMovementOverride > MesoRotation(meso_number)
> te.movement_id (the base program movement). Base program is never mutated —
only live-state rows (SlotMovementOverride, MesoRotation) change resolution.
NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import SQLModel, Session as DBSession, create_engine, select

from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import OverrideType
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


def _seed_ordering_program(db):
    """Program whose rows are inserted out of exercise_order for ordering tests."""
    prog = Program(name="Reorder Phase", phase="P1", duration_weeks=4)
    db.add(prog)
    db.commit()
    db.refresh(prog)

    day = ProgramDay(program_id=prog.id, day_index=0, day_role="D Reorder Test")
    db.add(day)
    db.commit()
    db.refresh(day)

    t1 = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    t2 = Tier(program_day_id=day.id, tier_label="T2", tier_order=2, tier_kind=TierKind.ACCESSORY)
    db.add(t1)
    db.add(t2)
    db.commit()
    db.refresh(t1)
    db.refresh(t2)

    movements = {
        "anchor_1": Movement(name="Anchor One [PB]", base_name="Anchor One"),
        "anchor_2": Movement(name="Anchor Two [PB]", base_name="Anchor Two"),
        "anchor_3": Movement(name="Anchor Three [PB]", base_name="Anchor Three"),
        "slot_1": Movement(name="Slot One [PB]", base_name="Slot One"),
        "slot_2": Movement(name="Slot Two [PB]", base_name="Slot Two"),
        "slot_3": Movement(name="Slot Three [PB]", base_name="Slot Three"),
    }
    for movement in movements.values():
        db.add(movement)
    db.commit()
    for movement in movements.values():
        db.refresh(movement)

    tes = {
        "anchor_1": TierExercise(
            tier_id=t1.id, slot_id="re_t1a", movement_id=movements["anchor_1"].id,
            exercise_order=1, tier_role="anchor",
        ),
        "anchor_2": TierExercise(
            tier_id=t1.id, slot_id="re_t1b", movement_id=movements["anchor_2"].id,
            exercise_order=2, tier_role="anchor",
        ),
        "anchor_3": TierExercise(
            tier_id=t1.id, slot_id="re_t1c", movement_id=movements["anchor_3"].id,
            exercise_order=3, tier_role="anchor",
        ),
        "slot_1": TierExercise(
            tier_id=t2.id, slot_id="re_t2a", movement_id=movements["slot_1"].id,
            exercise_order=1, tier_role="semi",
        ),
        "slot_2": TierExercise(
            tier_id=t2.id, slot_id="re_t2b", movement_id=movements["slot_2"].id,
            exercise_order=2, tier_role="semi",
        ),
        "slot_3": TierExercise(
            tier_id=t2.id, slot_id="re_t2c", movement_id=movements["slot_3"].id,
            exercise_order=3, tier_role="semi",
        ),
    }
    for key in ("anchor_3", "anchor_1", "anchor_2", "slot_3", "slot_1", "slot_2"):
        db.add(tes[key])
    db.commit()
    for te in tes.values():
        db.refresh(te)

    return {"movements": movements, "tes": tes}


def _add_reorder_override(db, te, source_note_id, override_order, active=True):
    db.add(SlotMovementOverride(
        tier_exercise_id=te.id,
        override_movement_id=te.movement_id,
        source_note_id=source_note_id,
        override_type=OverrideType.REORDER,
        override_order=override_order,
        active=active,
    ))


def test_skeleton_emits_base_movement_with_no_override():
    db = DBSession(_engine())
    ctx = _seed(db)
    sk = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk.anchor_movement_ids == [ctx["bench"].id]
    assert sk.adaptive_slots[0].program_movement_id == ctx["close_grip"].id


def test_skeleton_order_without_reorder_overrides_matches_base_exercise_order():
    db = DBSession(_engine())
    ctx = _seed_ordering_program(db)

    sk = lay_skeleton("D Reorder Test", db, meso_number=1)

    assert sk.anchor_movement_ids == [
        ctx["movements"]["anchor_1"].id,
        ctx["movements"]["anchor_2"].id,
        ctx["movements"]["anchor_3"].id,
    ]
    assert [meta.tier_exercise_id for meta in sk.anchor_meta] == [
        ctx["tes"]["anchor_1"].id,
        ctx["tes"]["anchor_2"].id,
        ctx["tes"]["anchor_3"].id,
    ]
    assert [slot.slot_id for slot in sk.adaptive_slots] == ["re_t2a", "re_t2b", "re_t2c"]
    assert [slot.program_movement_id for slot in sk.adaptive_slots] == [
        ctx["movements"]["slot_1"].id,
        ctx["movements"]["slot_2"].id,
        ctx["movements"]["slot_3"].id,
    ]


def test_skeleton_uses_active_reorder_override_for_effective_order():
    db = DBSession(_engine())
    ctx = _seed_ordering_program(db)
    note = Note(text="move reordered slots")
    db.add(note)
    db.commit()
    db.refresh(note)

    _add_reorder_override(db, ctx["tes"]["anchor_3"], note.id, override_order=1.5)
    _add_reorder_override(db, ctx["tes"]["anchor_2"], note.id, override_order=0.5, active=False)
    _add_reorder_override(db, ctx["tes"]["slot_3"], note.id, override_order=1.5)
    _add_reorder_override(db, ctx["tes"]["slot_1"], note.id, override_order=3.5)
    _add_reorder_override(db, ctx["tes"]["slot_2"], note.id, override_order=0.5, active=False)
    db.commit()

    sk = lay_skeleton("D Reorder Test", db, meso_number=1)

    assert sk.anchor_movement_ids == [
        ctx["movements"]["anchor_1"].id,
        ctx["movements"]["anchor_3"].id,
        ctx["movements"]["anchor_2"].id,
    ]
    assert [meta.tier_exercise_id for meta in sk.anchor_meta] == [
        ctx["tes"]["anchor_1"].id,
        ctx["tes"]["anchor_3"].id,
        ctx["tes"]["anchor_2"].id,
    ]
    assert [slot.slot_id for slot in sk.adaptive_slots] == ["re_t2c", "re_t2b", "re_t2a"]

    db.refresh(ctx["tes"]["slot_3"])
    assert ctx["tes"]["slot_3"].exercise_order == 3


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
    (adaptive) slots too, via _effective_movement_id. Prove it fires at
    meso_number=2 and does NOT fire at meso_number=1 (rotation is meso-gated,
    not always-on). Without this, a refactor could silently revert the
    adaptive branch to raw te.movement_id and stay green.

    (2026-08-11, STAB maintenance-block redesign, Task 3: this test previously used
    D4's d4_t2a, which carried a meso-2 rotation to Pendlay Row. D4's T2 GS was fully
    turned over per the FINAL doc and no longer carries any meso rotation -- repointed
    to D5's d5_t2b, the program's other adaptive-slot ("free" role) meso-rotation
    example, unaffected by that task.

    2026-08-12, Task 4: D5's own T2 GS is now ALSO fully turned over (d5_t2b
    no longer exists) -- there is no real adaptive-role meso rotation left
    anywhere in the program. This test now inserts a synthetic, test-only
    MesoRotation row directly on D5's real d5_t2h slot (Matrix Machine Bulgarian
    Split Squat, "free" role), mirroring test_generation_context.py's
    identical fix, rather than reading a real production rotation.
    """
    te = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d5_t2h")
    ).one()
    single_leg = gen_db.exec(
        select(Movement).where(Movement.base_name == "Reverse Hyper - Single Leg")
    ).one()
    assert single_leg.id != te.movement_id, \
        "the synthetic d5_t2h meso-2 rotation must be a real movement swap"
    gen_db.add(MesoRotation(tier_exercise_id=te.id, meso_number=2, movement_id=single_leg.id))
    gen_db.commit()
    mr = gen_db.exec(
        select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.meso_number == 2,
        )
    ).one()

    m2 = lay_skeleton("D5 Lower B", gen_db, meso_number=2)
    slot2 = next(s for s in m2.adaptive_slots if s.slot_id == "d5_t2h")
    assert slot2.program_movement_id == mr.movement_id, \
        "meso-2 adaptive slot resolves to the seeded MesoRotation target (Reverse Hyper - Single Leg)"

    m1 = lay_skeleton("D5 Lower B", gen_db, meso_number=1)
    slot1 = next(s for s in m1.adaptive_slots if s.slot_id == "d5_t2h")
    assert slot1.program_movement_id == te.movement_id, \
        "meso-1 adaptive slot emits the base movement — rotation is meso-gated"


def test_load_override_is_not_applied_as_a_movement_swap():
    """Task 1 (note-apply REDESIGN) guard: a LOAD/REPS override lives in the same
    table as a MOVEMENT swap, and its override_movement_id is a harmless
    placeholder (never the intended slot movement). lay_skeleton must filter to
    override_type == MOVEMENT so a LOAD row is NOT mistaken for a swap.

    The override_movement_id here is a DIFFERENT movement (incline, NOT the
    slot's own bench) precisely so that dropping the skeleton's MOVEMENT filter
    would flip the emitted anchor to incline and fail this test — that's the
    assertion that actually protects the filter.
    """
    db = DBSession(_engine())
    ctx = _seed(db)
    note = Note(text="add 10 lb to bench")
    db.add(note); db.commit(); db.refresh(note)

    load_ov = SlotMovementOverride(
        tier_exercise_id=ctx["bench_te"].id,
        override_movement_id=ctx["incline"].id,   # distinct movement — must be ignored
        source_note_id=note.id,
        override_type=OverrideType.LOAD,
        load_delta=10,
    )
    db.add(load_ov); db.commit()

    sk = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk.anchor_movement_ids == [ctx["bench"].id], \
        "a LOAD override must NOT swap the slot's movement — base movement stands"


def test_movement_and_load_overrides_coexist_on_one_slot():
    """Task 1 (note-apply REDESIGN): a slot may carry BOTH a MOVEMENT swap and a
    LOAD adjustment simultaneously (the generalized table enforces no uniqueness).
    lay_skeleton resolves the MOVEMENT row independently of the LOAD row — proving
    the symmetric override_type filters (skeleton MOVEMENT / assembler LOAD-REPS)
    keep the two concerns separate. (The +10 load application is covered by
    test_slot_override_apply.py; here we only assert the swap still fires.)
    """
    db = DBSession(_engine())
    ctx = _seed(db)
    note = Note(text="switch bench to incline AND add 10 lb")
    db.add(note); db.commit(); db.refresh(note)

    mv_ov = SlotMovementOverride(
        tier_exercise_id=ctx["bench_te"].id,
        override_movement_id=ctx["incline"].id,
        source_note_id=note.id,
        override_type=OverrideType.MOVEMENT,
    )
    load_ov = SlotMovementOverride(
        tier_exercise_id=ctx["bench_te"].id,
        override_movement_id=ctx["overhead_press"].id,  # placeholder — never a swap
        source_note_id=note.id,
        override_type=OverrideType.LOAD,
        load_delta=10,
    )
    db.add(mv_ov); db.add(load_ov); db.commit()

    sk = lay_skeleton("D1 Upper Push", db, meso_number=1)
    assert sk.anchor_movement_ids == [ctx["incline"].id], \
        "the MOVEMENT override must swap to incline even with a LOAD override present"


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
