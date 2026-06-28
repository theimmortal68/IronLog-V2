"""fallback.py — two-tier deterministic fallback (Fork 4c; §3A).

When repair exhausts (3 retries) OR offline, the engine self-produces a
guaranteed-valid session:
  1. If a prior COMPLETED session for this day_role exists → replay it with
     loads refreshed (last_valid_selections).
  2. Else (cold-start) → emit the program prior: each adaptive slot filled with
     its program_movement_id, which is valid + trainable by construction.

program_selections is also reused by Task 10's quiet-week deterministic path.

NO from __future__ import annotations (project-wide constraint).
"""
from typing import Optional

from sqlmodel import Session as DBSession, select

from ..models.enums import SessionStatus
from ..models.session import Session as WorkoutSession
from .assembler import AssembledSession, assemble
from .context import GenerationContext
from .proposer import Selections, SlotSelection
from .skeleton import Skeleton


def program_selections(skeleton: Skeleton) -> Selections:
    """Deterministic program emission (§3A): each adaptive slot filled with its
    program-prescribed movement (the prior). Used for cold-start fallback AND the
    quiet-week no-LLM path (Task 10).

    Skips slots whose program_movement_id is None (should not occur in a well-seeded
    program, but guarded for robustness).
    """
    slots = [
        SlotSelection(slot_id=s.slot_id, movement_id=s.program_movement_id)
        for s in skeleton.adaptive_slots
        if s.program_movement_id is not None
    ]
    return Selections(
        ordering=[s.slot_id for s in slots],
        slots=slots,
        rationale="deterministic program emission (the prior)",
    )


def last_valid_selections(
    skeleton: Skeleton,
    ctx: GenerationContext,
    db: DBSession,
) -> Optional[Selections]:
    """Reconstruct selections from the most recent COMPLETED session for this
    day_role.  Returns None if no prior COMPLETED session exists.

    Slot fills are inferred from the prior session's non-anchor exercises (in
    order_index order), matched positionally to the skeleton's adaptive slots
    with kind in ('giant', 'knee').  The returned Selections are then passed to
    assemble() so that loads are refreshed from the current MovementState — the
    prior session's target_load values are not reused.
    """
    prior: Optional[WorkoutSession] = db.exec(
        select(WorkoutSession)
        .where(
            WorkoutSession.day_role == skeleton.day_role,
            WorkoutSession.status == SessionStatus.COMPLETED,
        )
        .order_by(WorkoutSession.date.desc())
    ).first()

    if prior is None:
        return None

    # Identify the adaptive slots that are filled by the proposer (giant + knee).
    slot_iter = [s for s in skeleton.adaptive_slots if s.kind in ("giant", "knee")]
    if not slot_iter:
        return None

    # Flatten exercises from the prior session in group/exercise order, then
    # take the last N (non-anchor) to match positionally against slot_iter.
    exs = [
        e
        for g in sorted(prior.groups, key=lambda g: g.order_index)
        for e in sorted(g.exercises, key=lambda e: e.order_index)
    ]
    # Heuristic: the adaptive exercises are the last len(slot_iter) exercises
    # in the flat list (anchors come first per PRIMARY_NOT_FIRST invariant).
    non_anchor = exs[-len(slot_iter):] if len(exs) >= len(slot_iter) else exs

    slots = []
    order = []
    for spec, ex in zip(slot_iter, non_anchor):
        slots.append(SlotSelection(slot_id=spec.slot_id, movement_id=ex.movement_id))
        order.append(spec.slot_id)

    if not slots:
        return None

    return Selections(
        ordering=order,
        slots=slots,
        rationale="deterministic fallback (last valid, loads refreshed)",
    )


def fallback_session(
    skeleton: Skeleton,
    ctx: GenerationContext,
    db: DBSession,
) -> AssembledSession:
    """Produce a guaranteed-valid session via two-tier deterministic fallback.

    Tier 1: if a prior COMPLETED same-day_role session exists, replay it with
            loads refreshed from the current MovementState.
    Tier 2 (cold-start): emit the program prior — each adaptive slot filled with
            its program_movement_id, valid + trainable by construction.

    Re-assembles through assemble() so that all numeric prescriptions are
    freshly computed from the current engine state; no stale numbers are reused.
    """
    sel = last_valid_selections(skeleton, ctx, db) or program_selections(skeleton)
    return assemble(sel, skeleton, ctx, db)
