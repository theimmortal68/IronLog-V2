"""
skeleton.py — read the program definition and produce a Skeleton (the session prior).

lay_skeleton(day_role, db, meso_number=1) queries the program tables to build a
Skeleton: the T1 anchor movement(s) and a list of SlotSpecs (adaptive slots).
Each SlotSpec carries the program-prescribed movement (the prior) so that Task 3's
proposer can use it as the starting point for exercise selection.

NO from __future__ import annotations (project-wide constraint).
"""
from dataclasses import dataclass, field
from typing import List, Optional

from sqlmodel import Session, select

from ironlog.models.program import (
    MesoRotation, ProgramDay, Tier, TierExercise, TierKind,
)


@dataclass
class SlotSpec:
    """Specification for one adaptive exercise slot in a generated session."""
    slot_id: str
    kind: str                       # "knee" | "giant" | "accessory"
    pattern: Optional[str]
    tier_role: str                  # "anchor" | "semi" | "free"
    knee_modality: Optional[str]
    program_movement_id: Optional[int]   # the program prior for this slot
    group_key: str = ""             # tier_label of the source Tier; used by the
                                    # assembler to group giant slots into one
                                    # ExerciseGroup per tier (e.g. "T2 GS").


@dataclass
class Skeleton:
    """The session skeleton: anchors + adaptive slots derived from the program."""
    day_role: str
    anchor_movement_ids: List[int] = field(default_factory=list)
    adaptive_slots: List[SlotSpec] = field(default_factory=list)


def lay_skeleton(day_role: str, db: Session, meso_number: int = 1) -> Skeleton:
    """Read the program and return a Skeleton for the given day_role and meso.

    anchor_movement_ids:
        Movement ids for all TierExercises with tier_role=="anchor".
        Overridden by the matching MesoRotation row if one exists for meso_number.

    adaptive_slots:
        SlotSpec for every non-anchor TierExercise, with
        program_movement_id = te.movement_id (the meso-1 prior; no meso override
        applied to non-anchors — meso rotation for semi/free slots is handled
        by the Task 3 proposer layer, not the skeleton).
        kind = "knee" if knee_modality set,
               "giant" if tier is GIANT_SET,
               "accessory" otherwise.
    """
    prog_day = db.exec(
        select(ProgramDay).where(ProgramDay.day_role == day_role)
    ).first()
    if prog_day is None:
        raise ValueError(f"No ProgramDay found for day_role={day_role!r}")

    tiers = db.exec(
        select(Tier)
        .where(Tier.program_day_id == prog_day.id)
        .order_by(Tier.tier_order)
    ).all()

    anchor_movement_ids: List[int] = []
    adaptive_slots: List[SlotSpec] = []

    for tier in tiers:
        exercises = db.exec(
            select(TierExercise)
            .where(TierExercise.tier_id == tier.id)
            .order_by(TierExercise.exercise_order)
        ).all()

        for te in exercises:
            if te.tier_role == "anchor":
                # Check for a meso-specific rotation override
                mr = db.exec(
                    select(MesoRotation).where(
                        MesoRotation.tier_exercise_id == te.id,
                        MesoRotation.meso_number == meso_number,
                    )
                ).first()
                movement_id = mr.movement_id if mr is not None else te.movement_id
                anchor_movement_ids.append(movement_id)
            else:
                adaptive_slots.append(SlotSpec(
                    slot_id=te.slot_id,
                    kind=_slot_kind(te, tier),
                    pattern=te.pattern,
                    tier_role=te.tier_role,
                    knee_modality=te.knee_modality,
                    program_movement_id=te.movement_id,
                    group_key=tier.tier_label,
                ))

    return Skeleton(
        day_role=day_role,
        anchor_movement_ids=anchor_movement_ids,
        adaptive_slots=adaptive_slots,
    )


def _slot_kind(te: TierExercise, tier: Tier) -> str:
    """Compute the slot kind for a non-anchor TierExercise."""
    if te.knee_modality is not None:
        return "knee"
    if tier.tier_kind == TierKind.GIANT_SET:
        return "giant"
    return "accessory"
