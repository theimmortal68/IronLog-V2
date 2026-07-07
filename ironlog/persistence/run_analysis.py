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

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Callable, Hashable, List

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from ..engine.advance import SessionPerf, advance, roll_unassisted_max
from ..engine.analysis import (
    AnalysisContext,
    AnalysisResult,
    EngineStateInput,
    LoggedSet,
    MovementAnalysisInput,
    analyze_session,
)
from ..engine.calibration import evaluate_calibration_flip
from ..engine.e1rm import implied_rir
from ..engine.progression import resolve_objective
from ..engine.stall import STALL_WINDOW, build_stall_signal
from ..models.enums import CalibrationStatus, Objective
from ..models.library import E1rmHistory, EngineState, Movement, MovementState, PhasePolicy
from ..models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as WorkoutSession, SetLog,
)
from .apply import apply_analysis

logger = logging.getLogger(__name__)

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


def already_analyzed(session_id: int, db: DBSession) -> bool:
    """True iff session.analyzed_at is set — robust idempotency marker.

    Used as the idempotency guard in run_analysis (NAMED GATE b). The analyzed_at
    timestamp is stamped on the Session row in the SAME transaction as apply_analysis,
    covering ALL sessions — including those that produce no E1rmHistory anchor (e.g.
    warmup-only sessions).  The old E1rmHistory-based check would return False for
    anchor-less sessions, leaving run_analysis open to unlimited re-execution and
    potential double-advancement of consecutive_ceiling_sessions /
    consecutive_failed_progressions if the engine logic ever changes.

    Returns False for a session_id that does not exist (safe for standalone calls).
    The not-found guard in run_analysis (WorkoutSession.one()) still raises for
    unknown session_ids before this function is called.
    """
    session = db.exec(
        select(WorkoutSession).where(WorkoutSession.id == session_id)
    ).first()
    if session is None:
        return False
    return session.analyzed_at is not None


def _resolve_movement_state(db: DBSession, movement_id: int, day_id: str) -> MovementState:
    """Get-or-create with legacy-row adoption (composite (movement_id, day_id) key).

    Every pre-Task-1 row (and every existing test fixture seeded before the
    progression engine) has exactly one MovementState per movement_id, with
    day_id left at its None default. Naive "miss -> create a new row" would
    leave those fixtures with two rows per movement_id and break their
    movement_id-only `.one()` lookups (MultipleResultsFound). Instead:
    exact (movement_id, day_id) match first; else adopt the legacy
    (day_id IS NULL) row for this movement_id by stamping its day_id (this
    mirrors exactly what the Task-1 migration's own backfill does for real
    DB rows — consistent, not a hack); else create a fresh row.
    """
    state = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            MovementState.day_id == day_id,
        )
    ).first()
    if state is not None:
        return state
    legacy = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            col(MovementState.day_id).is_(None),
        )
    ).first()
    if legacy is not None:
        legacy.day_id = day_id
        db.add(legacy)
        db.flush()
        return legacy
    fresh = MovementState(movement_id=movement_id, day_id=day_id)
    db.add(fresh)
    db.flush()
    return fresh


def _build_session_perf(mid: int, movement: Movement, set_logs: List[SetLog],
                         planned_sets: dict) -> SessionPerf:
    """Build a SessionPerf for one movement from its raw SetLog/PlannedSet rows.

    Groups this movement's non-warmup SetLog rows by set_index — unilateral
    movements log two SetLog rows sharing the same set_index (one per side;
    there is no side-identifying column), bilateral movements have exactly
    one row per set_index (grouping degenerates trivially).
    """
    groups: dict = defaultdict(list)
    for sl in set_logs:
        if sl.movement_id != mid or sl.is_warmup:
            continue
        groups[sl.set_index].append(sl)

    def _group_hits(rows) -> bool:
        if not rows:
            return False
        for sl in rows:
            ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
            if ps is None or ps.target_reps_high is None:
                return False
            if sl.actual_reps is None or sl.actual_reps < ps.target_reps_high:
                return False
        return True

    hit_target = bool(groups) and all(_group_hits(rows) for rows in groups.values())
    all_sides_cleared = hit_target if movement.unilateral else True

    max_rpe = 0.0
    for rows in groups.values():
        for sl in rows:
            ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
            if sl.rpe_numeric is not None:
                v = sl.rpe_numeric
            elif ps is not None and ps.target_rpe is not None and sl.feedback_tap is not None:
                v = 10.0 - implied_rir(ps.target_rpe, sl.feedback_tap)
            else:
                continue
            if v > max_rpe:
                max_rpe = v

    last_set_hit_target = False
    if groups:
        max_idx = max(groups.keys())
        last_set_hit_target = _group_hits(groups[max_idx])

    unassisted_set1_reps = None
    if groups:
        min_idx = min(groups.keys())
        for sl in groups[min_idx]:
            if sl.actual_unassisted_reps is not None:
                unassisted_set1_reps = sl.actual_unassisted_reps
                break

    return SessionPerf(
        hit_target=hit_target,
        max_rpe=max_rpe,
        all_sides_cleared=all_sides_cleared,
        session_performed=True,  # this movement had logged sets this session
        last_set_hit_target=last_set_hit_target,
        unassisted_set1_reps=unassisted_set1_reps,
    )


def _confirmation_window(db: DBSession, mid: int, set_logs: List[SetLog],
                          planned_sets: dict) -> int:
    """T1 (incl. "T1b") -> 1; everything else, including an unresolvable
    label, -> 2 (the accessory default is the safe side)."""
    for sl in set_logs:
        if sl.movement_id != mid or sl.planned_set_id is None:
            continue
        ps = planned_sets.get(sl.planned_set_id)
        if ps is None:
            continue
        pex = db.exec(
            select(PlannedExercise).where(PlannedExercise.id == ps.planned_exercise_id)
        ).first()
        if pex is None:
            continue
        grp = db.exec(
            select(ExerciseGroup).where(ExerciseGroup.id == pex.group_id)
        ).first()
        if grp is None or grp.label is None:
            continue
        return 1 if grp.label.startswith("T1") else 2
    return 2


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

    Idempotency guard (NAMED GATE b): if already_analyzed returns True, this
    call is a no-op — returns an empty AnalysisResult without writing anything.
    This prevents duplicate E1rmHistory rows if the same session is logged twice
    (e.g., POST /sessions/{id}/log called more than once).
    """
    workout = db.exec(
        select(WorkoutSession).where(WorkoutSession.id == session_id)
    ).one()

    # Idempotency guard (NAMED GATE b): early-return if analysis already ran.
    # Placed after loading `workout` so the session-not-found error (.one()) still fires.
    if already_analyzed(session_id, db):
        return AnalysisResult()
    phase = db.exec(select(EngineState)).one().current_phase
    phase_default = db.exec(
        select(PhasePolicy).where(PhasePolicy.phase == phase)
    ).one().default_objective

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
    movement_by_mv = {}
    for mid in movement_ids:
        state = _resolve_movement_state(db, mid, workout.day_role)
        state_by_mv[mid] = state
        movement = db.exec(
            select(Movement).where(Movement.id == mid)
        ).one()
        movement_by_mv[mid] = movement
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
            objective=resolve_objective(movement.objective_override, phase_default),
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

    # v0.6 (Task 6): wire the progression engine's advance() onto each logged
    # movement. day_id is stamped on every delta unconditionally (apply_analysis
    # needs it to resolve the (movement_id, day_id) row regardless of whether
    # the advance step below succeeds). The rest of this block (SessionPerf
    # construction through advance() through the stall-signal write) is wrapped
    # per-movement in try/except: on failure, log it and leave that movement's
    # delta exactly as analyze_session produced it — "a broken engine step
    # reduces to your program yesterday" (design spec §9). Fallback invariant:
    # NEVER writes current_load; run_analysis must complete regardless.
    day_id = workout.day_role
    for d in result.movement_deltas:
        d.day_id = day_id
        mid = d.movement_id
        state = state_by_mv[mid]
        movement = movement_by_mv[mid]
        try:
            perf = _build_session_perf(mid, movement, set_logs, planned_sets)
            window = _confirmation_window(db, mid, set_logs, planned_sets)
            adv = advance(movement.progression_rule, state, perf, movement, window)

            new_unassisted_max_rolling = None
            if perf.unassisted_set1_reps is not None:
                new_unassisted_max_rolling = roll_unassisted_max(
                    state.unassisted_max_rolling, perf.unassisted_set1_reps
                )

            consecutive_failed_for_stall = (
                d.new_consecutive_failed if d.new_consecutive_failed is not None
                else state.consecutive_failed_progressions
            )
            prior_rows = db.exec(
                select(E1rmHistory).where(E1rmHistory.movement_id == mid)
            ).all()
            progress_e1rms = select_progress_window(list(prior_rows))
            stall = build_stall_signal(
                movement_id=mid,
                day_id=day_id,
                consecutive_failed=consecutive_failed_for_stall,
                progress_e1rms=progress_e1rms,
                current_load=state.current_load,
                limiting_muscle=movement.primary_muscle,
            )
            if adv.advanced:
                stall = None  # clear on advance regardless of what build_stall_signal returned

            # Commit computed values onto the delta only after the whole step
            # succeeded — a mid-way exception must leave no partial writes.
            d.active_rule = adv.active_rule
            d.new_consecutive_advance_count = adv.consecutive_advance_count
            if adv.new_tier is not None:
                d.new_tier = adv.new_tier
            if adv.new_assist_level is not None:
                d.new_assist_level = adv.new_assist_level
            if adv.new_rep_target is not None:
                d.new_rep_target = adv.new_rep_target
            if adv.new_body_position is not None:
                d.new_body_position = adv.new_body_position
            if adv.earned_load_step is not None:
                # K2: stage the earned load step (never current_load). commit_session
                # applies it to current_load and clears the marker (apply-once).
                d.pending_load_delta = adv.earned_load_step
            if new_unassisted_max_rolling is not None:
                d.new_unassisted_max_rolling = new_unassisted_max_rolling
            d.stall_signal_computed = True
            d.stall_signal = stall
        except Exception:
            logger.exception(
                "progression-engine advance step failed for movement_id=%s session_id=%s",
                mid, session_id,
            )

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

    # Stamp analyzed_at BEFORE apply_analysis so the marker is committed atomically
    # with the MovementState deltas and any E1rmHistory rows.  This covers ALL sessions
    # (anchor-present and anchor-less) and makes already_analyzed a true no-op gate
    # on any subsequent call for this session_id.
    workout.analyzed_at = datetime.now(timezone.utc)
    db.add(workout)

    apply_analysis(
        result, db,
        session_id=session_id,
        phase=phase,
        calibration_flips=frozenset(flips),
    )
    return result
