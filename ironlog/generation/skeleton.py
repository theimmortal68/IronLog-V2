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

from ironlog.models.enums import OverrideType
from ironlog.models.program import (
    MesoRotation, ProgramDay, SlotMovementOverride, Tier, TierExercise, TierKind,
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
    is_giant_tier: bool = False     # True when the source Tier is GIANT_SET,
                                    # independent of kind/knee_modality or
                                    # anchor role.
    group_key: str = ""             # tier_label of the source Tier; used by the
                                    # assembler to group giant slots into one
                                    # ExerciseGroup per tier (e.g. "T2 GS").
    # Task 3 (assembler fidelity): the TierExercise's literal rep targets/RPE cap
    # and the source Tier's rest_seconds, carried alongside the slot so the
    # assembler can source PlannedSet numbers from the seeded program data
    # instead of hardcoding them.
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    rpe_cap: Optional[float] = None
    rest_seconds: Optional[int] = None
    # The source Tier's shoe label (display-only session-graph metadata; see
    # AnchorSpec.shoe below) — carried alongside rest_seconds/group_key so the
    # assembler can set ExerciseGroup.shoe for the client's shoe-swap cue.
    shoe: Optional[str] = None
    # Task 1 (note-apply REDESIGN): this slot's TierExercise.id, so the assembler
    # can look up an active LOAD/REPS SlotMovementOverride at prescription time.
    tier_exercise_id: Optional[int] = None


@dataclass
class AnchorSpec:
    """Per-anchor TierExercise/Tier metadata, parallel to Skeleton.anchor_movement_ids
    (same index order — both lists are appended together in lay_skeleton's single
    pass over tiers/exercises, so zip(anchor_movement_ids, anchor_meta) is safe)."""
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    rpe_cap: Optional[float] = None
    rest_seconds: Optional[int] = None
    # The source Tier's tier_label (e.g. "T1"); carried alongside rest_seconds so
    # the assembler can set ExerciseGroup.label (client tier-aware rest reads it).
    tier_label: Optional[str] = None
    # The source Tier's shoe label (display-only; the client's shoe-swap cue).
    shoe: Optional[str] = None
    # Task 1 (note-apply REDESIGN): this anchor's TierExercise.id, so the assembler
    # can look up an active LOAD/REPS SlotMovementOverride at prescription time.
    tier_exercise_id: Optional[int] = None


@dataclass
class Skeleton:
    """The session skeleton: anchors + adaptive slots derived from the program."""
    day_role: str
    anchor_movement_ids: List[int] = field(default_factory=list)
    # Task 3: parallel to anchor_movement_ids (same order/length).
    anchor_meta: List[AnchorSpec] = field(default_factory=list)
    adaptive_slots: List[SlotSpec] = field(default_factory=list)


def lay_skeleton(day_role: str, db: Session, meso_number: int = 1,
                 program_id: Optional[int] = None) -> Skeleton:
    """Read the program and return a Skeleton for the given day_role and meso.

    program_id (Fork 3 scoping): when given, the ProgramDay is filtered to that
    program. When None, falls back to the active program (EngineState.
    active_program_id) if set; otherwise to the single matching ProgramDay by
    day_role alone (back-compat for existing single-program callers/tests).

    anchor_movement_ids:
        Movement ids for TierExercises with tier_role=="anchor" outside
        GIANT_SET tiers.
        Resolved via _effective_movement_id: an active SlotMovementOverride
        takes precedence, then the matching MesoRotation row for meso_number,
        then te.movement_id.

    adaptive_slots:
        SlotSpec for every non-anchor TierExercise, plus anchor exercises
        inside GIANT_SET tiers, with
        program_movement_id resolved via the same _effective_movement_id
        precedence (SlotMovementOverride > MesoRotation(meso_number) >
        te.movement_id).
        kind = "knee" if knee_modality set,
               "giant" if tier is GIANT_SET and tier_role is not anchor,
               "accessory" otherwise.
        is_giant_tier is set directly from the source Tier so knee-priority
        slots and anchor-role slots inside GIANT_SET tiers still assemble into
        the shared tier group.
    """
    if program_id is None:
        from ironlog.models.library import EngineState
        es = db.get(EngineState, 1)
        if es is not None and es.active_program_id is not None:
            program_id = es.active_program_id

    stmt = select(ProgramDay).where(ProgramDay.day_role == day_role)
    if program_id is not None:
        stmt = stmt.where(ProgramDay.program_id == program_id)
    prog_day = db.exec(stmt).first()
    if prog_day is None:
        raise ValueError(f"No ProgramDay found for day_role={day_role!r}")

    tiers = db.exec(
        select(Tier)
        .where(Tier.program_day_id == prog_day.id)
        .order_by(Tier.tier_order)
    ).all()

    anchor_movement_ids: List[int] = []
    anchor_meta: List[AnchorSpec] = []
    adaptive_slots: List[SlotSpec] = []

    for tier in tiers:
        exercises = db.exec(
            select(TierExercise)
            .where(TierExercise.tier_id == tier.id)
        ).all()
        exercises = sorted(exercises, key=lambda te: _effective_exercise_order(db, te))

        for te in exercises:
            if te.tier_role == "anchor" and tier.tier_kind != TierKind.GIANT_SET:
                movement_id = _effective_movement_id(db, te, meso_number)
                anchor_movement_ids.append(movement_id)
                anchor_meta.append(AnchorSpec(
                    rep_low=te.rep_low, rep_high=te.rep_high,
                    rpe_cap=te.rpe_cap, rest_seconds=tier.rest_seconds,
                    tier_label=tier.tier_label, shoe=tier.shoe,
                    tier_exercise_id=te.id,
                ))
            else:
                adaptive_slots.append(SlotSpec(
                    slot_id=te.slot_id,
                    kind=_slot_kind(te, tier),
                    pattern=te.pattern,
                    tier_role=te.tier_role,
                    knee_modality=te.knee_modality,
                    program_movement_id=_effective_movement_id(db, te, meso_number),
                    is_giant_tier=tier.tier_kind == TierKind.GIANT_SET,
                    group_key=tier.tier_label,
                    rep_low=te.rep_low, rep_high=te.rep_high, rpe_cap=te.rpe_cap,
                    rest_seconds=tier.rest_seconds, shoe=tier.shoe,
                    tier_exercise_id=te.id,
                ))

    return Skeleton(
        day_role=day_role,
        anchor_movement_ids=anchor_movement_ids,
        anchor_meta=anchor_meta,
        adaptive_slots=adaptive_slots,
    )


def _effective_exercise_order(db: Session, te: TierExercise) -> float:
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.override_type == OverrideType.REORDER,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is not None:
        return ov.override_order
    return float(te.exercise_order)


def _effective_movement_id(db: Session, te: TierExercise, meso_number: int) -> int:
    """Resolve the movement id for a TierExercise slot.

    Precedence: active SlotMovementOverride > MesoRotation(meso_number) > te.movement_id.
    Base program (te.movement_id) is never mutated by this resolution.

    Filters to override_type == MOVEMENT: SlotMovementOverride is now a general
    slot override (Task 1, note-apply REDESIGN) that also carries LOAD/REPS
    adjustments applied by the assembler at prescription time, NOT here. An
    active LOAD/REPS row on the same tier_exercise_id must not be mistaken for
    a movement swap (its override_movement_id is a harmless placeholder, not
    the intended slot movement).
    """
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.override_type == OverrideType.MOVEMENT,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is not None:
        return ov.override_movement_id
    mr = db.exec(select(MesoRotation).where(
        MesoRotation.tier_exercise_id == te.id,
        MesoRotation.meso_number == meso_number)).first()
    return mr.movement_id if mr is not None else te.movement_id


def _slot_kind(te: TierExercise, tier: Tier) -> str:
    """Compute the slot kind for a TierExercise routed into adaptive_slots
    (non-anchor, or an anchor whose tier is GIANT_SET)."""
    if te.knee_modality is not None:
        return "knee"
    if tier.tier_kind == TierKind.GIANT_SET and te.tier_role != "anchor":
        return "giant"
    return "accessory"
