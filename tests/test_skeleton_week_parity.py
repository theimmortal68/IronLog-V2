"""tests/test_skeleton_week_parity.py — lay_skeleton honors WeekParityRotation.

Precedence under test: active SlotMovementOverride > WeekParityRotation(as_of)
> MesoRotation(meso_number) > te.movement_id. WeekParityRotation is calendar
driven and can override rep targets without mutating the base program.
NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

from sqlmodel import SQLModel, Session as DBSession, create_engine

from ironlog.generation.skeleton import _effective_movement_id, lay_skeleton, week_parity
from ironlog.models.enums import OverrideType
from ironlog.models.library import Movement
from ironlog.models.program import (
    MesoRotation, Program, ProgramDay, SlotMovementOverride, Tier, TierExercise,
    TierKind, WeekParityRotation,
)
from ironlog.models.session import Note
import ironlog.models  # register tables


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _seed(db):
    """Program with one anchor slot and one adaptive slot."""
    prog = Program(name="Week Parity Phase", phase="P1", duration_weeks=4)
    db.add(prog); db.commit(); db.refresh(prog)
    day = ProgramDay(program_id=prog.id, day_index=1, day_role="D Week Parity")
    db.add(day); db.commit(); db.refresh(day)

    t1 = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    t2 = Tier(program_day_id=day.id, tier_label="T2", tier_order=2, tier_kind=TierKind.ACCESSORY)
    db.add(t1); db.add(t2); db.commit(); db.refresh(t1); db.refresh(t2)

    movements = {
        "anchor_base": Movement(name="Week Anchor Base [PB]", base_name="Week Anchor Base"),
        "anchor_even": Movement(name="Week Anchor Even [PB]", base_name="Week Anchor Even"),
        "anchor_override": Movement(name="Week Anchor Override [PB]", base_name="Week Anchor Override"),
        "slot_base": Movement(name="Week Slot Base [PB]", base_name="Week Slot Base"),
        "slot_even": Movement(name="Week Slot Even [PB]", base_name="Week Slot Even"),
        "slot_meso": Movement(name="Week Slot Meso [PB]", base_name="Week Slot Meso"),
        "slot_odd": Movement(name="Week Slot Odd [PB]", base_name="Week Slot Odd"),
    }
    for movement in movements.values():
        db.add(movement)
    db.commit()
    for movement in movements.values():
        db.refresh(movement)

    anchor_te = TierExercise(
        tier_id=t1.id, slot_id="wp_t1", movement_id=movements["anchor_base"].id,
        exercise_order=1, tier_role="anchor", rep_low=5, rep_high=8,
    )
    slot_te = TierExercise(
        tier_id=t2.id, slot_id="wp_t2a", movement_id=movements["slot_base"].id,
        exercise_order=1, tier_role="semi", rep_low=10, rep_high=12,
    )
    db.add(anchor_te); db.add(slot_te); db.commit(); db.refresh(anchor_te); db.refresh(slot_te)

    return {
        "movements": movements,
        "anchor_te": anchor_te,
        "slot_te": slot_te,
    }


def _only_slot(sk):
    assert len(sk.adaptive_slots) == 1
    return sk.adaptive_slots[0]


def test_week_parity_helper_alternates_by_epoch_weeks():
    epoch_monday = date(2026, 1, 5)
    next_epoch_week = date(2026, 1, 12)

    assert week_parity(epoch_monday) != week_parity(next_epoch_week)
    assert week_parity(epoch_monday) == "A"
    assert week_parity(next_epoch_week) == "B"


def test_week_parity_helper_alternates_across_iso_53_week_boundary():
    iso_week_53 = date(2026, 12, 28)
    iso_week_1 = date(2027, 1, 4)

    assert iso_week_53.isocalendar()[1] == 53
    assert iso_week_1.isocalendar()[1] == 1
    assert week_parity(iso_week_53) != week_parity(iso_week_1)


def test_no_week_parity_rows_preserves_base_movement_and_reps():
    db = DBSession(_engine())
    ctx = _seed(db)

    sk = lay_skeleton("D Week Parity", db, as_of=date(2026, 1, 5))
    slot = _only_slot(sk)

    assert sk.anchor_movement_ids == [ctx["movements"]["anchor_base"].id]
    assert sk.anchor_meta[0].rep_low == ctx["anchor_te"].rep_low
    assert sk.anchor_meta[0].rep_high == ctx["anchor_te"].rep_high
    assert slot.program_movement_id == ctx["movements"]["slot_base"].id
    assert slot.rep_low == ctx["slot_te"].rep_low
    assert slot.rep_high == ctx["slot_te"].rep_high


def test_week_parity_rotation_resolves_a_and_b_movements_and_reps():
    db = DBSession(_engine())
    ctx = _seed(db)
    even_monday = date(2026, 1, 5)  # ISO week 2, verified below.
    odd_monday = date(2026, 1, 12)  # ISO week 3, verified below.
    assert even_monday.isocalendar()[1] == 2
    assert odd_monday.isocalendar()[1] == 3

    db.add(WeekParityRotation(
        tier_exercise_id=ctx["slot_te"].id,
        week_parity="A",
        movement_id=ctx["movements"]["slot_even"].id,
        rep_low=6,
        rep_high=8,
    ))
    db.add(WeekParityRotation(
        tier_exercise_id=ctx["slot_te"].id,
        week_parity="B",
        movement_id=ctx["movements"]["slot_odd"].id,
        rep_low=12,
        rep_high=15,
    ))
    db.commit()

    sk_a = lay_skeleton("D Week Parity", db, as_of=even_monday)
    slot_a = _only_slot(sk_a)
    assert slot_a.program_movement_id == ctx["movements"]["slot_even"].id
    assert slot_a.rep_low == 6
    assert slot_a.rep_high == 8

    sk_b = lay_skeleton("D Week Parity", db, as_of=odd_monday)
    slot_b = _only_slot(sk_b)
    assert slot_b.program_movement_id == ctx["movements"]["slot_odd"].id
    assert slot_b.rep_low == 12
    assert slot_b.rep_high == 15


def test_week_parity_rotation_missing_current_parity_falls_back_to_base_movement():
    db = DBSession(_engine())
    ctx = _seed(db)

    db.add(WeekParityRotation(
        tier_exercise_id=ctx["slot_te"].id,
        week_parity="A",
        movement_id=ctx["movements"]["slot_even"].id,
        rep_low=6,
        rep_high=8,
    ))
    db.commit()

    sk = lay_skeleton("D Week Parity", db, as_of=date(2026, 1, 12))
    slot = _only_slot(sk)

    assert slot.program_movement_id == ctx["movements"]["slot_base"].id
    assert slot.rep_low == ctx["slot_te"].rep_low
    assert slot.rep_high == ctx["slot_te"].rep_high


def test_week_parity_rotation_precedes_meso_and_missing_parity_uses_meso():
    db = DBSession(_engine())
    ctx = _seed(db)

    db.add(MesoRotation(
        tier_exercise_id=ctx["slot_te"].id,
        meso_number=2,
        movement_id=ctx["movements"]["slot_meso"].id,
    ))
    db.add(WeekParityRotation(
        tier_exercise_id=ctx["slot_te"].id,
        week_parity="A",
        movement_id=ctx["movements"]["slot_even"].id,
        rep_low=6,
        rep_high=8,
    ))
    db.commit()

    sk_a = lay_skeleton("D Week Parity", db, meso_number=2, as_of=date(2026, 1, 5))
    slot_a = _only_slot(sk_a)
    assert slot_a.program_movement_id == ctx["movements"]["slot_even"].id
    assert slot_a.rep_low == 6
    assert slot_a.rep_high == 8

    sk_b = lay_skeleton("D Week Parity", db, meso_number=2, as_of=date(2026, 1, 12))
    slot_b = _only_slot(sk_b)
    assert slot_b.program_movement_id == ctx["movements"]["slot_meso"].id
    assert slot_b.rep_low == ctx["slot_te"].rep_low
    assert slot_b.rep_high == ctx["slot_te"].rep_high


def test_effective_movement_id_compatibility_ignores_week_parity_rotation():
    db = DBSession(_engine())
    ctx = _seed(db)

    db.add(WeekParityRotation(
        tier_exercise_id=ctx["slot_te"].id,
        week_parity="A",
        movement_id=ctx["movements"]["slot_even"].id,
    ))
    db.add(WeekParityRotation(
        tier_exercise_id=ctx["slot_te"].id,
        week_parity="B",
        movement_id=ctx["movements"]["slot_odd"].id,
    ))
    db.commit()

    assert _effective_movement_id(db, ctx["slot_te"], meso_number=1) == ctx["movements"]["slot_base"].id


def test_week_parity_rotation_with_no_rep_override_preserves_tier_exercise_reps():
    db = DBSession(_engine())
    ctx = _seed(db)

    db.add(WeekParityRotation(
        tier_exercise_id=ctx["anchor_te"].id,
        week_parity="A",
        movement_id=ctx["movements"]["anchor_even"].id,
    ))
    db.commit()

    sk = lay_skeleton("D Week Parity", db, as_of=date(2026, 1, 5))

    assert sk.anchor_movement_ids == [ctx["movements"]["anchor_even"].id]
    assert sk.anchor_meta[0].rep_low == ctx["anchor_te"].rep_low
    assert sk.anchor_meta[0].rep_high == ctx["anchor_te"].rep_high


def test_active_slot_movement_override_takes_precedence_over_week_parity_rotation():
    db = DBSession(_engine())
    ctx = _seed(db)
    note = Note(text="switch week anchor")
    db.add(note); db.commit(); db.refresh(note)

    db.add(WeekParityRotation(
        tier_exercise_id=ctx["anchor_te"].id,
        week_parity="A",
        movement_id=ctx["movements"]["anchor_even"].id,
        rep_low=2,
        rep_high=3,
    ))
    db.add(SlotMovementOverride(
        tier_exercise_id=ctx["anchor_te"].id,
        override_movement_id=ctx["movements"]["anchor_override"].id,
        source_note_id=note.id,
        override_type=OverrideType.MOVEMENT,
    ))
    db.commit()

    sk = lay_skeleton("D Week Parity", db, as_of=date(2026, 1, 5))

    assert sk.anchor_movement_ids == [ctx["movements"]["anchor_override"].id]
    assert sk.anchor_meta[0].rep_low == ctx["anchor_te"].rep_low
    assert sk.anchor_meta[0].rep_high == ctx["anchor_te"].rep_high
