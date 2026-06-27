"""
run_analysis.py — the deterministic analyze->apply seam (v0.5 spec §7).

Resolves context for a logged session, runs the pure analysis, buckets e1RM
history into weekly estimates via a CALLER-SUPPLIED week_keyer (no calendar math
here beyond applying the callable), evaluates calibration flips, and calls the
single-write-point applier once. Writes nothing itself.

No HTTP. v0.6 generation calls this seam. detect_stall is NOT called here (it's
a v0.6 consumer); select_progress_window is the pure helper v0.6 will use to
feed it. Cold-start is expected: until ~3 PROGRESS sessions log, the analyzers
are data-starved — this is correct, not broken.
"""

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Callable, Hashable, List

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from ..engine.analysis import (
    AnalysisContext,
    AnalysisResult,
    EngineStateInput,
    LoggedSet,
    MovementAnalysisInput,
    analyze_session,
)
from ..engine.calibration import evaluate_calibration_flip
from ..engine.stall import STALL_WINDOW
from ..models.enums import CalibrationStatus, Objective
from ..models.library import E1rmHistory, EngineState, Movement, MovementState
from ..models.session import PlannedSet, Session as WorkoutSession, SetLog
from .apply import apply_analysis

WeekKey = Hashable


def select_progress_window(
    history_rows: List[E1rmHistory],
    window: int = STALL_WINDOW,
) -> List[float]:
    """The last `window` PROGRESS-objective anchor e1RMs, oldest-first.

    Window-selection is the caller's job (detect_stall takes pre-filtered e1RMs).
    Interleaved MAINTAIN/MEASURE rows are excluded. v0.6 feeds this to detect_stall.
    """
    progress = [r for r in history_rows if r.objective == Objective.PROGRESS]
    progress.sort(key=lambda r: r.computed_at)
    return [r.e1rm for r in progress[-window:]]


def _weekly_max_estimates(
    rows: List[E1rmHistory],
    session_date_by_id: dict,
    week_keyer: Callable[[date], WeekKey],
) -> List[float]:
    """Bucket history rows by week_keyer(session date), aggregate each week by
    max, return estimates ordered by week key (chronological)."""
    by_week: dict = defaultdict(list)
    for r in rows:
        wk = week_keyer(session_date_by_id[r.session_id])
        by_week[wk].append(r.e1rm)
    return [max(by_week[wk]) for wk in sorted(by_week)]


def run_analysis(
    session_id: int,
    db: DBSession,
    week_keyer: Callable[[date], WeekKey],
) -> AnalysisResult:
    """Analyze one logged session and apply the results (single transaction).

    Field-name note: SetLog records only actuals; target prescription fields
    (target_rpe, target_reps_low, target_reps_high) live on PlannedSet. This
    function joins via SetLog.planned_set_id to resolve them. If a SetLog has
    no planned_set_id (unlinked set), target fields default to None and the set
    will not qualify as an anchor for e1RM estimation.
    """
    workout = db.exec(
        select(WorkoutSession).where(WorkoutSession.id == session_id)
    ).one()
    phase = db.exec(select(EngineState)).one().current_phase

    set_logs = db.exec(
        select(SetLog).where(SetLog.session_id == session_id)
    ).all()
    movement_ids = sorted({sl.movement_id for sl in set_logs})

    # Load planned sets for target prescription data.
    # SetLog.target_rpe / target_reps_* don't exist on SetLog; they're on PlannedSet.
    planned_set_ids = [sl.planned_set_id for sl in set_logs if sl.planned_set_id is not None]
    planned_sets: dict = {}
    if planned_set_ids:
        for ps in db.exec(
            select(PlannedSet).where(col(PlannedSet.id).in_(planned_set_ids))
        ).all():
            planned_sets[ps.id] = ps

    # Build per-movement analysis inputs from current state + this session's sets.
    movements_inputs = []
    state_by_mv = {}
    for mid in movement_ids:
        state = db.exec(
            select(MovementState).where(MovementState.movement_id == mid)
        ).one()
        state_by_mv[mid] = state
        movement = db.exec(
            select(Movement).where(Movement.id == mid)
        ).one()
        logged = []
        for sl in set_logs:
            if sl.movement_id != mid:
                continue
            ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
            logged.append(LoggedSet(
                actual_load=sl.actual_load,
                actual_reps=sl.actual_reps,
                feedback_tap=sl.feedback_tap,
                is_warmup=sl.is_warmup,
                target_rpe=ps.target_rpe if ps else None,
                target_reps_low=ps.target_reps_low if ps else None,
                target_reps_high=ps.target_reps_high if ps else None,
            ))
        movements_inputs.append(MovementAnalysisInput(
            movement_id=mid,
            objective=movement.objective_override or Objective.MAINTAIN,
            current_tier=state.current_increment_tier,
            increment_ladder_len=len(movement.increment_ladder or [1]),
            consecutive_ceiling_sessions=state.consecutive_ceiling_sessions,
            consecutive_failed_progressions=state.consecutive_failed_progressions,
            logged_sets=logged,
        ))

    ctx = AnalysisContext(
        movements=movements_inputs,
        engine_state=EngineStateInput(current_phase=phase),
    )
    result = analyze_session(ctx)

    # Calibration flips: for each CALIBRATING lift, build weekly-max estimates
    # including this session's just-computed e1RM (synthetic in-memory row; the
    # applier writes the real row). week_keyer is applied — no calendar math here.
    flips: set = set()
    session_date_by_id: dict = {workout.id: workout.date}
    for d in result.movement_deltas:
        if d.new_e1rm is None:
            continue
        state = state_by_mv[d.movement_id]
        if state.calibration_status != CalibrationStatus.CALIBRATING:
            continue
        prior = db.exec(
            select(E1rmHistory).where(E1rmHistory.movement_id == d.movement_id)
        ).all()
        for r in prior:
            if r.session_id not in session_date_by_id:
                session_date_by_id[r.session_id] = db.exec(
                    select(WorkoutSession).where(WorkoutSession.id == r.session_id)
                ).one().date
        # Synthetic row mirrors what the applier will persist for the current session.
        synthetic = E1rmHistory(
            movement_id=d.movement_id,
            session_id=workout.id,
            e1rm=d.new_e1rm,
            objective=d.objective or Objective.MAINTAIN,
            phase=phase,
            anchor_load=d.anchor_load,
            anchor_reps=d.anchor_reps,
            anchor_rpe=d.anchor_rpe,
            computed_at=datetime.now(timezone.utc),
        )
        weekly = _weekly_max_estimates(
            list(prior) + [synthetic],
            session_date_by_id,
            week_keyer,
        )
        if evaluate_calibration_flip(weekly, state.calibration_status):
            flips.add(d.movement_id)

    apply_analysis(
        result, db,
        session_id=session_id,
        phase=phase,
        calibration_flips=frozenset(flips),
    )
    return result
