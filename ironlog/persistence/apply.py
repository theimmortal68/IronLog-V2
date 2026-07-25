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
from ..models.library import E1rmHistory, HtProgressionState, MovementState


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
    # Keyed on (movement_id, day_id): callers on the composite-key path (v0.6+,
    # run_analysis) stamp d.day_id before calling apply_analysis; every older
    # caller/test leaves it at the None default, and `col == None` on a
    # SQLAlchemy where() clause translates to IS NULL, so this is backward
    # compatible with every pre-existing delta/fixture.
    states = {
        (d.movement_id, d.day_id): db.exec(
            select(MovementState).where(
                MovementState.movement_id == d.movement_id,
                MovementState.day_id == d.day_id,
            )
        ).one()
        for d in result.movement_deltas
    }
    now = datetime.now(timezone.utc)
    for d in result.movement_deltas:
        state = states[(d.movement_id, d.day_id)]
        if d.new_e1rm is not None:
            state.e1rm = d.new_e1rm
            state.e1rm_updated_at = now
            # In real flow anchor_load is non-None whenever new_e1rm is non-None:
            # _analyze_movement co-populates new_e1rm and the anchor fields atomically
            # from _best_e1rm_set's qualifying anchor (which requires load/reps/rpe all
            # present). The anchor_load guard here only fires for artificial test deltas
            # that lack anchor details — it prevents NOT NULL violations without masking
            # a real skip. Do not set new_e1rm without the anchor fields in production code.
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
        # v0.6 (Task 6): progression-engine earned-state writes. Never
        # current_load — that stays commit_session's exclusive job.
        if d.new_assist_level is not None:
            state.assist_level = d.new_assist_level
        if d.new_rep_target is not None:
            state.current_rep_target = d.new_rep_target
        if d.new_body_position is not None:
            state.current_body_position = d.new_body_position
        if d.active_rule is not None:
            state.active_rule = d.active_rule
        if d.new_consecutive_advance_count is not None:
            state.consecutive_advance_count = d.new_consecutive_advance_count
        if d.new_unassisted_max_rolling is not None:
            state.unassisted_max_rolling = d.new_unassisted_max_rolling
        if d.pending_load_delta is not None:
            # K2 advance->load bridge: stage the earned load step. This is NOT
            # current_load (commit_session remains its sole writer) — it is the
            # additive marker commit_session reads, applies once, and clears.
            state.pending_load_delta = d.pending_load_delta
        if d.pending_ht_plates is not None:
            if d.pending_ht_unified_group is not None:
                ht_row = db.exec(
                    select(HtProgressionState).where(
                        HtProgressionState.movement_id == d.movement_id,
                        HtProgressionState.unified_ht_group == d.pending_ht_unified_group,
                    )
                ).one()
                ht_row.pending_ht_plates = d.pending_ht_plates
                ht_row.pending_ht_band_config = d.pending_ht_band_config
                db.add(ht_row)
            else:
                state.pending_ht_plates = d.pending_ht_plates
                state.pending_ht_band_config = d.pending_ht_band_config
        if d.stall_signal_computed:
            # None is a valid WRITE here (clears the signal on advance) —
            # distinct from every other new_* field's "None = don't touch".
            state.stall_signal = d.stall_signal
        db.add(state)
    db.commit()
