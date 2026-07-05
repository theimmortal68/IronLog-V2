"""apply.py — note-apply: resolve a note to its program slot + create a
live-state SlotMovementOverride. Deterministic; NO LLM in this path.
NO from __future__ import annotations."""
from sqlmodel import Session as DBSession, select

from ..models.library import EngineState
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
    # Fork 3 scoping (mirrors lay_skeleton): filter ProgramDay to the active
    # program when one is set; otherwise fall back to all programs (back-compat
    # for single-program setups so day_role alone still resolves).
    program_id = None
    es = db.get(EngineState, 1)
    if es is not None and es.active_program_id is not None:
        program_id = es.active_program_id
    stmt = select(ProgramDay).where(ProgramDay.day_role == ws.day_role)
    if program_id is not None:
        stmt = stmt.where(ProgramDay.program_id == program_id)
    days = db.exec(stmt).all()
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


def apply_override(note, tier_exercise_id, override_type, db: DBSession, *,
                    override_movement_id=None, load_delta=None, load_absolute=None,
                    rep_low=None, rep_high=None) -> SlotMovementOverride:
    """Create a live-state SlotMovementOverride for an EXPLICIT slot + override
    the client has already confirmed. Deterministic; NO LLM in this path.

    Unlike the old note-based apply, this does NOT infer the slot from the
    note (see resolve_slot, still used elsewhere) — the caller sends
    tier_exercise_id directly, so there is no ambiguity to reject.

    Raises SlotResolutionError (-> 404) if the TierExercise or (for MOVEMENT)
    the target movement doesn't exist. Raises ValueError (-> 400) for a
    malformed per-type payload (LOAD needs exactly one of load_delta /
    load_absolute; REPS needs rep_low and/or rep_high). On success, stamps
    note.confirmed/applied and returns the created override.
    """
    from ..models.library import Movement
    from ..models.enums import OverrideType

    te = db.get(TierExercise, tier_exercise_id)
    if te is None:
        raise SlotResolutionError(f"slot {tier_exercise_id} not found")

    ot = OverrideType(override_type)  # raises ValueError -> caller maps 400

    kw = dict(tier_exercise_id=tier_exercise_id, source_note_id=note.id,
              active=True, override_type=ot)
    if ot == OverrideType.MOVEMENT:
        if override_movement_id is None or db.get(Movement, override_movement_id) is None:
            raise SlotResolutionError("target movement not found")
        kw["override_movement_id"] = override_movement_id
    elif ot == OverrideType.LOAD:
        if (load_delta is None) == (load_absolute is None):
            raise ValueError("exactly one of load_delta / load_absolute required")
        # override_movement_id is NOT NULL on the table (Task 1 convention): set
        # it to the slot's own movement_id as a harmless placeholder, never read
        # for a LOAD override.
        kw["override_movement_id"] = te.movement_id
        kw["load_delta"], kw["load_absolute"] = load_delta, load_absolute
    elif ot == OverrideType.REPS:
        if rep_low is None and rep_high is None:
            raise ValueError("rep_low and/or rep_high required")
        kw["override_movement_id"] = te.movement_id
        kw["rep_low"], kw["rep_high"] = rep_low, rep_high

    ov = SlotMovementOverride(**kw)
    db.add(ov)
    note.confirmed = True
    note.applied = True
    db.add(note)
    db.commit()
    db.refresh(ov)
    return ov
