"""
apply.py — the single write point for analysis results.

Reads an AnalysisResult (computed by the pure engine.analysis) and writes the
proposed MovementState deltas to the DB. This is the ONLY place analysis output
becomes persistent state, mirroring the validator's "engine computes, caller
applies" contract for writes.

Atomic by construction: all MovementState rows are resolved FIRST, so a missing
row raises before any mutation — no partial write. Never writes current_phase
(phase_transition_available is report-only). The v0.5 e1RM-history append hooks
into the new_e1rm branch in one line.
"""

from datetime import datetime, timezone

from sqlmodel import Session, select

from ..engine.analysis import AnalysisResult
from ..models.library import MovementState


def apply_analysis(result: AnalysisResult, db: Session) -> None:
    """Apply an AnalysisResult's MovementState deltas. The single write point."""
    # Resolve every row first — a missing row raises here, before any mutation.
    states = {
        d.movement_id: db.exec(
            select(MovementState).where(MovementState.movement_id == d.movement_id)
        ).one()
        for d in result.movement_deltas
    }
    now = datetime.now(timezone.utc)
    for d in result.movement_deltas:
        state = states[d.movement_id]
        if d.new_e1rm is not None:
            state.e1rm = d.new_e1rm
            state.e1rm_updated_at = now
            # v0.5 SEAM: append (d.movement_id, d.new_e1rm, now) to the e1RM history here.
        if d.new_tier is not None:
            state.current_increment_tier = d.new_tier
        if d.new_consecutive_ceiling is not None:
            state.consecutive_ceiling_sessions = d.new_consecutive_ceiling
        if d.new_consecutive_failed is not None:
            state.consecutive_failed_progressions = d.new_consecutive_failed
        db.add(state)
    db.commit()
