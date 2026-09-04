"""
skeleton.py — read the program definition and produce a Skeleton (the session prior).

lay_skeleton(day_role, db, meso_number=1) queries the program tables to build a
Skeleton: the T1 anchor movement(s) and a list of SlotSpecs (adaptive slots).
Each SlotSpec carries the program-prescribed movement (the prior) so that Task 3's
proposer can use it as the starting point for exercise selection.

NO from __future__ import annotations (project-wide constraint).
"""
from dataclasses import dataclass, field
from datetime import date
import logging
from typing import List, Optional

from sqlmodel import Session, select

from ironlog.models.enums import OverrideType
from ironlog.models.program import (
    MesoRotation, ProgramDay, SlotMovementOverride, Tier, TierExercise, TierKind,
    MicrocycleParityRotation,
)

logger = logging.getLogger(__name__)


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
    pair_key: str = ""              # Stable key shared by two linked non-giant
                                    # tiers that should assemble as one
                                    # alternating pair group.
    # Task 3 (assembler fidelity): the TierExercise's literal rep targets/RPE cap
    # and the source Tier's rest_seconds, carried alongside the slot so the
    # assembler can source PlannedSet numbers from the seeded program data
    # instead of hardcoding them.
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    duration_low_seconds: Optional[int] = None
    duration_high_seconds: Optional[int] = None
    rpe_cap: Optional[float] = None
    rest_seconds: Optional[int] = None
    # The source Tier's shoe label (display-only session-graph metadata; see
    # AnchorSpec.shoe below) — carried alongside rest_seconds/group_key so the
    # assembler can set ExerciseGroup.shoe for the client's shoe-swap cue.
    shoe: Optional[str] = None
    # Task 1 (note-apply REDESIGN): this slot's TierExercise.id, so the assembler
    # can look up an active LOAD/REPS SlotMovementOverride at prescription time.
    tier_exercise_id: Optional[int] = None
    # Tier-order fix: the source Tier's Tier.tier_order (1-based position within
    # the day), so the assembler can sort ALL groups (anchor + giant + straight
    # adaptive) by their TRUE position in the day instead of assuming anchors
    # are always first. group_key/tier_label are display strings, not sortable.
    tier_order: Optional[int] = None


@dataclass
class AnchorSpec:
    """Per-anchor TierExercise/Tier metadata, parallel to Skeleton.anchor_movement_ids
    (same index order — both lists are appended together in lay_skeleton's single
    pass over tiers/exercises, so zip(anchor_movement_ids, anchor_meta) is safe)."""
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    duration_low_seconds: Optional[int] = None
    duration_high_seconds: Optional[int] = None
    rpe_cap: Optional[float] = None
    rest_seconds: Optional[int] = None
    # The source Tier's tier_label (e.g. "T1"); carried alongside rest_seconds so
    # the assembler can set ExerciseGroup.label (client tier-aware rest reads it).
    tier_label: Optional[str] = None
    # Stable key shared by two linked non-giant tiers that should assemble as
    # one alternating pair group.
    pair_key: str = ""
    # The source Tier's shoe label (display-only; the client's shoe-swap cue).
    shoe: Optional[str] = None
    # Task 1 (note-apply REDESIGN): this anchor's TierExercise.id, so the assembler
    # can look up an active LOAD/REPS SlotMovementOverride at prescription time.
    tier_exercise_id: Optional[int] = None
    # Tier-order fix: the source Tier's Tier.tier_order (1-based position within
    # the day) -- see SlotSpec.tier_order for why this is needed (anchors are
    # not always first; a trailing anchor tier, e.g. D2/D5's T4, must sort by
    # its true position, not be forced to the front).
    tier_order: Optional[int] = None


@dataclass
class Skeleton:
    """The session skeleton: anchors + adaptive slots derived from the program."""
    day_role: str
    anchor_movement_ids: List[int] = field(default_factory=list)
    # Task 3: parallel to anchor_movement_ids (same order/length).
    anchor_meta: List[AnchorSpec] = field(default_factory=list)
    adaptive_slots: List[SlotSpec] = field(default_factory=list)


@dataclass
class _ResolvedSlot:
    movement_id: int
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    duration_low_seconds: Optional[int] = None
    duration_high_seconds: Optional[int] = None


def microcycle_parity(microcycle_ordinal: int) -> str:
    """"A" or "B" from a Microcycle ordinal."""
    return "A" if microcycle_ordinal % 2 == 0 else "B"


def week_parity(as_of: date) -> str:
    """"A" or "B" from a fixed Monday-anchored week count.

    This intentionally does not use raw ISO week-number parity: ISO years with
    week 53 would otherwise produce the same letter for the final week of one
    ISO year and the first week of the next.
    """
    # Arbitrary fixed Monday anchor; it only needs to be a Monday so week
    # boundaries land cleanly and the alternation is stable forever.
    epoch = date(2026, 1, 5)
    return "A" if ((as_of - epoch).days // 7) % 2 == 0 else "B"


def lay_skeleton(day_role: str, db: Session, meso_number: int = 1,
                 program_id: Optional[int] = None,
                 as_of: Optional[date] = None,
                 microcycle_ordinal: Optional[int] = None,
                 mesocycle_id: Optional[int] = None) -> Skeleton:
    """Read the program and return a Skeleton for the given day_role and meso.

    program_id (Fork 3 scoping): when given, the ProgramDay is filtered to that
    program. When None, falls back to the active program (EngineState.
    active_program_id) if set; otherwise to the single matching ProgramDay by
    day_role alone (back-compat for existing single-program callers/tests).

    as_of:
        Calendar date used for MicrocycleParityRotation resolution. When None,
        defaults once per call to date.today().

    anchor_movement_ids:
        Movement ids for TierExercises with tier_role=="anchor" outside
        GIANT_SET tiers.
        Resolved via _resolve_slot: an active SlotMovementOverride takes
        precedence, then the matching MicrocycleParityRotation row for as_of's
        parity, then the matching MesoRotation row for meso_number, then
        te.movement_id.

    adaptive_slots:
        SlotSpec for every non-anchor TierExercise, plus anchor exercises
        inside GIANT_SET tiers, with
        program_movement_id resolved via the same _resolve_slot precedence
        (SlotMovementOverride > MicrocycleParityRotation(as_of) >
        MesoRotation(meso_number) > te.movement_id).
        kind = "knee" if knee_modality set,
               "giant" if tier is GIANT_SET and tier_role is not anchor,
               "accessory" otherwise.
        is_giant_tier is set directly from the source Tier so knee-priority
        slots and anchor-role slots inside GIANT_SET tiers still assemble into
        the shared tier group.
    """
    effective_as_of = as_of if as_of is not None else date.today()

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
        pair_key = _pair_key_for_tier(db, tier, exercises)

        for te in exercises:
            resolved = _resolve_slot(db, te, meso_number, effective_as_of, microcycle_ordinal, mesocycle_id)
            rep_low = resolved.rep_low if resolved.rep_low is not None else te.rep_low
            rep_high = resolved.rep_high if resolved.rep_high is not None else te.rep_high
            duration_low_seconds = (
                resolved.duration_low_seconds
                if resolved.duration_low_seconds is not None
                else te.duration_low_seconds
            )
            duration_high_seconds = (
                resolved.duration_high_seconds
                if resolved.duration_high_seconds is not None
                else te.duration_high_seconds
            )
            if te.tier_role == "anchor" and tier.tier_kind != TierKind.GIANT_SET:
                anchor_movement_ids.append(resolved.movement_id)
                anchor_meta.append(AnchorSpec(
                    rep_low=rep_low, rep_high=rep_high,
                    duration_low_seconds=duration_low_seconds,
                    duration_high_seconds=duration_high_seconds,
                    rpe_cap=te.rpe_cap, rest_seconds=tier.rest_seconds,
                    tier_label=tier.tier_label, pair_key=pair_key,
                    shoe=tier.shoe,
                    tier_exercise_id=te.id, tier_order=tier.tier_order,
                ))
            else:
                adaptive_slots.append(SlotSpec(
                    slot_id=te.slot_id,
                    kind=_slot_kind(te, tier),
                    pattern=te.pattern,
                    tier_role=te.tier_role,
                    knee_modality=te.knee_modality,
                    program_movement_id=resolved.movement_id,
                    is_giant_tier=tier.tier_kind == TierKind.GIANT_SET,
                    group_key=tier.tier_label, pair_key=pair_key,
                    rep_low=rep_low, rep_high=rep_high, rpe_cap=te.rpe_cap,
                    duration_low_seconds=duration_low_seconds,
                    duration_high_seconds=duration_high_seconds,
                    rest_seconds=tier.rest_seconds, shoe=tier.shoe,
                    tier_exercise_id=te.id, tier_order=tier.tier_order,
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


def _resolve_slot(db: Session, te: TierExercise, meso_number: int, as_of: date, microcycle_ordinal: Optional[int] = None, mesocycle_id: Optional[int] = None) -> _ResolvedSlot:
    """Resolve the movement id and optional rep overrides for a TierExercise slot.

    Precedence: active SlotMovementOverride > MicrocycleParityRotation(as_of) >
    MesoRotation(meso_number) > te.movement_id.
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
        return _ResolvedSlot(movement_id=ov.override_movement_id)

    parity_val = microcycle_parity(microcycle_ordinal) if microcycle_ordinal is not None else week_parity(as_of)
    wpr = db.exec(select(MicrocycleParityRotation).where(
        MicrocycleParityRotation.tier_exercise_id == te.id,
        MicrocycleParityRotation.week_parity == parity_val)).first()
    if wpr is not None:
        return _ResolvedSlot(
            movement_id=wpr.movement_id,
            rep_low=wpr.rep_low,
            rep_high=wpr.rep_high,
        )

    if mesocycle_id is not None:
        mr = db.exec(select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.mesocycle_id == mesocycle_id)).first()
    else:
        mr = db.exec(select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.meso_number == meso_number)).first()
    if mr is not None:
        return _ResolvedSlot(movement_id=mr.movement_id)

    return _ResolvedSlot(movement_id=te.movement_id)


def _pair_key_for_tier(db: Session, tier: Tier, exercises: List[TierExercise]) -> str:
    if tier.paired_tier_id is None:
        return ""

    partner = db.get(Tier, tier.paired_tier_id)
    if partner is None:
        logger.warning(
            "Tier %s (%s) paired_tier_id=%s is missing; assembling as straight",
            tier.id, tier.tier_label, tier.paired_tier_id,
        )
        return ""

    if partner.program_day_id != tier.program_day_id:
        logger.warning(
            "Tier %s (%s) paired_tier_id=%s points outside program_day_id=%s; "
            "assembling as straight",
            tier.id, tier.tier_label, partner.id, tier.program_day_id,
        )
        return ""

    if tier.tier_kind == TierKind.GIANT_SET or partner.tier_kind == TierKind.GIANT_SET:
        logger.warning(
            "Tier %s (%s) pair points at a GIANT_SET tier; assembling as straight",
            tier.id, tier.tier_label,
        )
        return ""

    if tier.tier_kind != TierKind.PAIR and partner.tier_kind != TierKind.PAIR:
        logger.warning(
            "Tier %s (%s) has paired_tier_id=%s but neither side is PAIR; "
            "assembling as straight",
            tier.id, tier.tier_label, partner.id,
        )
        return ""

    if len(exercises) != 1:
        raise ValueError(
            f"PAIR tier {tier.tier_label!r} (id={tier.id}) must have exactly "
            f"one TierExercise; found {len(exercises)}"
        )

    if partner.paired_tier_id != tier.id:
        logger.warning(
            "Tier %s (%s) pair is not symmetric with tier %s (%s); the "
            "partner side will resolve to an empty pair_key and degrade to "
            "STRAIGHT, so no real pair forms here either",
            tier.id, tier.tier_label, partner.id, partner.tier_label,
        )

    # Fable review (2026-09-01): min(id) alone collides if corrupted data
    # ever has a THIRD tier one-sidedly pointing at one side of a real
    # pair (A<->B symmetric, C->A one-sided) -- C would silently merge
    # into A/B's group under a bare min-id key. Keying on both ids makes
    # that data error produce its own (harmless, single-exercise, then
    # STRAIGHT-degraded) key instead.
    return f"pair:{min(tier.id, partner.id)}:{max(tier.id, partner.id)}"


def _effective_movement_id(db: Session, te: TierExercise, meso_number: int, mesocycle_id: Optional[int] = None) -> int:
    """Resolve the movement id for a TierExercise slot.

    Compatibility wrapper for imports outside skeleton.py. This intentionally
    preserves the pre-MicrocycleParityRotation behavior used by notes/resolver.py:
    active SlotMovementOverride > MesoRotation(meso_number) > te.movement_id.
    """
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.override_type == OverrideType.MOVEMENT,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is not None:
        return ov.override_movement_id
    if mesocycle_id is not None:
        mr = db.exec(select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.mesocycle_id == mesocycle_id)).first()
    else:
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
