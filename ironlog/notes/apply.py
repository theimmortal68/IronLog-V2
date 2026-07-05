"""apply.py — note-apply: resolve a note to its program slot + create a
live-state SlotMovementOverride. Deterministic; NO LLM in this path.
NO from __future__ import annotations."""
from sqlmodel import Session as DBSession, select

from ..models.program import ProgramDay, Tier, TierExercise, SlotMovementOverride
from ..models.session import Note, Session as WorkoutSession


class SlotResolutionError(Exception):
    """No program slot matches the note's (day_role, movement_id)."""


class AmbiguousSlotError(Exception):
    """More than one slot matches — apply is rejected rather than guessing."""


def resolve_slot(note, db: DBSession) -> TierExercise:
    ws = db.get(WorkoutSession, note.session_id) if note.session_id else None
    if ws is None:
        raise SlotResolutionError("note has no session")
    days = db.exec(select(ProgramDay).where(ProgramDay.day_role == ws.day_role)).all()
    tier_ids = []
    for d in days:
        tier_ids += [t.id for t in db.exec(select(Tier).where(Tier.program_day_id == d.id)).all()]
    matches = []
    for tid in tier_ids:
        matches += db.exec(select(TierExercise).where(
            TierExercise.tier_id == tid,
            TierExercise.movement_id == note.movement_id)).all()
    if not matches:
        raise SlotResolutionError(f"no slot for movement {note.movement_id} in day {ws.day_role!r}")
    if len(matches) > 1:
        raise AmbiguousSlotError(f"{len(matches)} slots match; cannot auto-apply")
    return matches[0]
