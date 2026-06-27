"""
apply.py — the single write point for analysis results.

Reads an AnalysisResult (computed by the pure engine.analysis) and writes the
proposed MovementState deltas to the DB. This is the ONLY place analysis output
becomes persistent state, mirroring the validator's "engine computes, caller
applies" contract for writes.

Atomic by construction: all MovementState rows are resolved FIRST, so a missing
row raises before any mutation — no partial write. Never writes current_phase
(phase_transition_available is report-only). Never writes current_load
(generation's job — the two-writer boundary holds by construction).

The v0.5 e1RM-history append and calibration flip are gated on the new keyword
arguments; with defaults (old call shape) behavior is unchanged.
"""

from datetime import datetime, timezone
from typing import FrozenSet, Optional

from sqlmodel import Session, select

from ..engine.analysis import AnalysisResult
from ..models.enums import CalibrationStatus, Phase
from ..models.library import E1rmHistory, MovementState


def apply_analysis(
    result: AnalysisResult,
    db: Session,
    *,
    session_id: Optional[int] = None,
    phase: Optional[Phase] = None,
    calibration_flips: FrozenSet[int] = frozenset(),
) -> None:
    """Apply an AnalysisResult's MovementState deltas. The single write point.

    When session_id and phase are supplied (the run_analysis path), also append
    one E1rmHistory row per movement that has an anchor (new_e1rm is not None),
    stamped with objective/phase/anchor details. Flips calibration_status to
    MEASURED for any movement_id in calibration_flips. Never writes current_load.
    """
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
            if session_id is not None and phase is not None and d.anchor_load is not None:
                db.add(E1rmHistory(
                    movement_id=d.movement_id,
                    session_id=session_id,
                    e1rm=d.new_e1rm,
                    objective=d.objective,
                    phase=phase,
                    anchor_load=d.anchor_load,
                    anchor_reps=d.anchor_reps,
                    anchor_rpe=d.anchor_rpe,
                    computed_at=now,
                ))
        if d.new_tier is not None:
            state.current_increment_tier = d.new_tier
        if d.new_consecutive_ceiling is not None:
            state.consecutive_ceiling_sessions = d.new_consecutive_ceiling
        if d.new_consecutive_failed is not None:
            state.consecutive_failed_progressions = d.new_consecutive_failed
        if d.movement_id in calibration_flips:
            state.calibration_status = CalibrationStatus.MEASURED
        db.add(state)
    db.commit()
