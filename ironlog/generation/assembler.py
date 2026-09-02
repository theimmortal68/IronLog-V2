"""assembler.py — deterministic glue: selections -> Session (Fork 6). No new math.

Turns the LLM's selections into a full in-memory Session (groups → exercises → sets)
with every number computed via existing engine logic.  Computes the prospective
current_load for each movement but does NOT write it — that is committed at approve
(Task 9).

Public API:
  resolve_start_load(movement, state, db) -> Optional[float]   — Pin 1: real load, or None (needs-calibration)
  assemble(selections, skeleton, ctx, db) -> AssembledSession

NO from __future__ import annotations (project-wide constraint).
"""
from dataclasses import dataclass, field
from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session as DBSession, select

from ..engine.band_composite import (
    Band, config_peak, ht_performed_floor, resolved_band_config,
)
from ..engine.loading import clamp_to_cap, round_to_achievable, round_up_to_step
from ..engine.progression import resolve_objective
from ..models.enums import (
    GroupType, LiftCategory, ProgressionMode, ProgressionRule, Scheme,
    SessionStatus, SetRole,
)
from ..models.library import (
    BandPair, EngineState, Equipment, HtProgressionState, Movement,
    MovementState,
)
from ..models.program import DayFinisher, FinisherLog, ProgramDay, Tier, TierExercise
from ..models.session import ExerciseGroup, PlannedExercise, PlannedSet, SetLog
from ..models.session import Session as WorkoutSession
from .context import GenerationContext
from .load_trust import LoadTrust, compute_load_trust
from .proposer import Selections
from .skeleton import Skeleton

logger = logging.getLogger(__name__)


@dataclass
class AssembledSession:
    """The fully assembled in-memory session returned by assemble()."""
    session: WorkoutSession
    prospective_current_loads: Dict[int, float] = field(default_factory=dict)
    # HT (band-composite) prospective setup — movement_id -> (plates, config).
    # Mirrors prospective_current_loads: computed here, written only by
    # commit_session at approval (Option-C two-writer boundary).
    prospective_ht_setups: Dict[int, Tuple[float, list]] = field(default_factory=dict)
    # Unified HT prospective setup — (movement_id, unified_ht_group) -> (plates, config).
    prospective_ht_unified: Dict[Tuple[int, str], Tuple[float, list]] = field(default_factory=dict)
    warmup: Optional[Dict[str, Any]] = None
    finisher: Optional[Dict[str, Any]] = None


def _program_day_id_from_skeleton(skeleton: Skeleton, db: DBSession) -> Optional[int]:
    """Resolve the ProgramDay id through the skeleton's TierExercise FK metadata.

    Rest days have no tiers, so they fall through to a ProgramDay lookup and
    return a rest-day id with no DayFinisher row. Training days normally resolve
    through TierExercise -> Tier -> ProgramDay, so meso movement swaps do not
    affect the finisher lookup.
    """
    tier_exercise_ids: List[int] = []
    tier_exercise_ids.extend(
        meta.tier_exercise_id
        for meta in skeleton.anchor_meta
        if meta.tier_exercise_id is not None
    )
    tier_exercise_ids.extend(
        slot.tier_exercise_id
        for slot in skeleton.adaptive_slots
        if slot.tier_exercise_id is not None
    )
    for tier_exercise_id in tier_exercise_ids:
        tier_exercise = db.get(TierExercise, tier_exercise_id)
        if tier_exercise is None:
            continue
        tier = db.get(Tier, tier_exercise.tier_id)
        if tier is not None:
            return tier.program_day_id

    stmt = select(ProgramDay).where(ProgramDay.day_role == skeleton.day_role)
    engine_state = db.get(EngineState, 1)
    if engine_state is not None and engine_state.active_program_id is not None:
        stmt = stmt.where(ProgramDay.program_id == engine_state.active_program_id)
    program_day = db.exec(stmt.order_by(ProgramDay.day_index)).first()
    return program_day.id if program_day is not None else None


def _finisher_state(
    db: DBSession,
    movement_id: int,
    day_role: Optional[str],
) -> Optional[MovementState]:
    if day_role:
        state = db.exec(
            select(MovementState).where(
                MovementState.movement_id == movement_id,
                MovementState.day_id == day_role,
            )
        ).first()
        if state is not None:
            return state
    state = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            MovementState.day_id == None,  # noqa: E711
        )
    ).first()
    if state is not None:
        return state
    return db.exec(
        select(MovementState).where(MovementState.movement_id == movement_id)
    ).first()


def build_finisher_payload(
    db: DBSession,
    program_day_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Build the generated-session finisher payload for one ProgramDay."""
    if program_day_id is None:
        return None
    finisher = db.exec(
        select(DayFinisher).where(DayFinisher.program_day_id == program_day_id)
    ).first()
    if finisher is None:
        return None

    movement = db.get(Movement, finisher.movement_id)
    program_day = db.get(ProgramDay, program_day_id)
    state = _finisher_state(
        db,
        finisher.movement_id,
        program_day.day_role if program_day is not None else None,
    )
    current_duration_seconds = None
    current_rope = None
    if (
        movement is not None
        and movement.progression_rule == ProgressionRule.FINISHER_DURATION_THEN_ROPE.value
    ):
        current_duration_seconds = (
            state.current_duration_seconds if state is not None else None
        )
        current_rope = state.current_rope if state is not None else None

    # "Last logged value" prefill comes straight from the most recent
    # FinisherLog row for this movement, not from MovementState.current_load.
    # MovementState-based reuse was the source of a real bug: an unvalidated
    # movement_id could silently overwrite a LADDER movement's real working
    # load (MovementState is keyed by (movement_id, day_id=day_role), the
    # SAME namespace run_analysis.py uses for real progression state), and
    # separately, legacy day_id=None MovementState rows for
    # FINISHER_DURATION_THEN_ROPE movements (jump_rope) were being shadowed
    # by a sparse day-scoped row created for the "last logged weight" write.
    # FinisherLog is a brand-new table with no legacy rows, so this sidesteps
    # both problems entirely.
    last_log = db.exec(
        select(FinisherLog)
        .where(FinisherLog.movement_id == finisher.movement_id)
        .order_by(FinisherLog.performed_at.desc())
    ).first()

    return {
        "exercise_name": movement.name if movement is not None else "",
        "movement_id": finisher.movement_id,
        "duration_minutes": finisher.duration_minutes,
        "params": dict(finisher.params or {}),
        "current_duration_seconds": current_duration_seconds,
        "current_rope": current_rope,
        "last_logged_weight_lb": last_log.actual_weight_lb if last_log is not None else None,
        "last_logged_resistance_level": (
            last_log.actual_resistance_level if last_log is not None else None
        ),
    }


def build_warmup_payload(
    db: DBSession,
    program_day_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Static, unprogressed per-day warmup block -- no state, no movement FK."""
    if program_day_id is None:
        return None
    program_day = db.get(ProgramDay, program_day_id)
    if program_day is None or program_day.warmup_config is None:
        return None
    return dict(program_day.warmup_config)


# ---------------------------------------------------------------------------
# Pin 1: single source for a movement's working load
# ---------------------------------------------------------------------------

def resolve_start_load(movement: Movement, state: Optional[MovementState],
                       db: DBSession) -> Optional[float]:
    """Single source for a movement's working load (Pin 1) — a thin wrapper over
    the compute_load_trust keystone.

    Generation is HONEST about unconfigured loads:
      FRESH / STALE -> the real load (result.value); generation prescribes it.
      UNKNOWN       -> None (needs-calibration); NEVER an equipment/movement floor.

    Returning None signals the caller that this movement's sets must be flagged
    needs-calibration (target_load=None) rather than assembled at a fake floor.
    Bodyweight movements (PROTOCOL/CONDITIONING/NONE) are FRESH with value None —
    they legitimately carry no external load, also surfaced as None.

    The derived-ratio value resolution and the floor decision both live inside
    compute_load_trust now; this resolver no longer owns any anchor/floor logic.
    """
    result = compute_load_trust(movement, state, db, as_of=datetime.utcnow())
    if result.trust == LoadTrust.UNKNOWN:
        return None   # needs-calibration — never floor
    return result.value


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _step_and_floor(movement: Movement, db: DBSession):
    """Return (step, floor) from the movement's own fields or its equipment."""
    eq = db.get(Equipment, movement.load_equipment_id) if movement.load_equipment_id else None
    step = movement.min_step or (eq.min_step if eq else None) or 2.5
    floor = movement.load_floor if movement.load_floor is not None else (
        eq.load_floor if eq else None
    )
    return step, floor


def _sets_for_scheme(scheme: Scheme, load: Optional[float],
                     ctx: GenerationContext,
                     rep_low: Optional[int] = None, rep_high: Optional[int] = None,
                     rpe_cap: Optional[float] = None,
                     duration_low_seconds: Optional[int] = None,
                     duration_high_seconds: Optional[int] = None) -> List[PlannedSet]:
    """Map scheme -> a concrete list of PlannedSets.

    TOPSET_BACKOFF: TOP set + one BACKOFF at 90% (reps/RPE still sourced from
    the phase policy — unchanged; no Phase-1 T1 uses this scheme anymore).
    Everything else (STRAIGHT / DOUBLE_PROGRESSION / etc.): 3 WORKING sets whose
    reps/RPE come from the seeded TierExercise (rep_low/rep_high/rpe_cap), NOT
    the phase-policy band — Task 3 fidelity fix. Falls back to the old defaults
    (8-12 reps, phase-policy RPE / 8.0) only when the TE fields are None (should
    not happen for a fully-reconciled program, but keeps structural degeneracy
    safe rather than crashing).

    load is None for a needs-calibration (or bodyweight) movement: the sets still
    assemble structurally but carry target_load=None — never a fabricated floor.
    """
    pol = ctx.phase_policy
    backoff_load = round(load * 0.9, 1) if load is not None else None
    has_duration = duration_low_seconds is not None or duration_high_seconds is not None
    if scheme == Scheme.TOPSET_BACKOFF:
        if has_duration:
            return [
                PlannedSet(
                    set_index=0, set_role=SetRole.TOP,
                    target_load=load,
                    target_duration_low_seconds=duration_low_seconds,
                    target_duration_high_seconds=duration_high_seconds,
                    target_rpe=pol.top_set_rpe,
                ),
                PlannedSet(
                    set_index=1, set_role=SetRole.BACKOFF,
                    target_load=backoff_load,
                    target_duration_low_seconds=duration_low_seconds,
                    target_duration_high_seconds=duration_high_seconds,
                    target_rpe=pol.rpe_band_low,
                ),
            ]
        return [
            PlannedSet(
                set_index=0, set_role=SetRole.TOP,
                target_load=load,
                target_reps_low=3, target_reps_high=5,
                target_rpe=pol.top_set_rpe,
            ),
            PlannedSet(
                set_index=1, set_role=SetRole.BACKOFF,
                target_load=backoff_load,
                target_reps_low=5, target_reps_high=8,
                target_rpe=pol.rpe_band_low,
            ),
        ]
    # Default: 3 WORKING sets — reps/RPE sourced from the seeded TierExercise.
    lo = rep_low if rep_low is not None else (None if has_duration else 8)
    hi = rep_high if rep_high is not None else (None if has_duration else 12)
    rpe = rpe_cap if rpe_cap is not None else (
        pol.rpe_band_high if pol.rpe_band_high is not None else 8.0
    )
    return [
        PlannedSet(
            set_index=i, set_role=SetRole.WORKING,
            target_load=load,
            target_reps_low=lo, target_reps_high=hi,
            target_duration_low_seconds=duration_low_seconds,
            target_duration_high_seconds=duration_high_seconds,
            target_rpe=rpe,
        )
        for i in range(3)
    ]


def _apply_slot_override(db: DBSession, tier_exercise_id: Optional[int],
                         load: Optional[float], rep_low: Optional[int],
                         rep_high: Optional[int]) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """Apply an active LOAD/REPS SlotMovementOverride for this slot's
    TierExercise at prescription time (Task 1, note-apply REDESIGN, Option-C).

    Adjusts only the PRESCRIBED load/rep values fed into _sets_for_scheme —
    it NEVER writes current_load or MovementState. MOVEMENT-type overrides are
    handled by lay_skeleton (already, via _effective_movement_id) and are
    ignored here.
    """
    if tier_exercise_id is None:
        return load, rep_low, rep_high
    from ..models.enums import OverrideType
    from ..models.program import SlotMovementOverride
    # Symmetric with skeleton._effective_movement_id's MOVEMENT filter: the
    # generalized table can hold multiple active rows per tier_exercise_id (no
    # uniqueness enforced), so a slot may carry BOTH a MOVEMENT swap and a
    # LOAD/REPS adjustment. Filter to LOAD/REPS here so a bare .first() cannot
    # return the MOVEMENT row and silently drop the load/rep adjustment —
    # movement and load/rep overrides are resolved independently.
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == tier_exercise_id,
        SlotMovementOverride.override_type.in_((OverrideType.LOAD, OverrideType.REPS)),
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is None:
        return load, rep_low, rep_high
    if ov.override_type == OverrideType.LOAD:
        if ov.load_absolute is not None:
            load = ov.load_absolute
        elif ov.load_delta is not None and load is not None:
            load = load + ov.load_delta
    elif ov.override_type == OverrideType.REPS:
        if ov.rep_low is not None:
            rep_low = ov.rep_low
        if ov.rep_high is not None:
            rep_high = ov.rep_high
    return load, rep_low, rep_high


def _apply_ht_load_override(db: DBSession, tier_exercise_id: Optional[int],
                             plates: float) -> float:
    """Apply an active LOAD SlotMovementOverride to HT's resolved plates
    (Option-C: never writes MovementState/ht_plates -- adjusts only the
    in-memory value fed into ht_next_setup, same pattern as
    _apply_slot_override's scalar-load adjustment).

    HT resolves its current setup from MovementState.ht_plates, not the
    scalar `load` _apply_slot_override already adjusts -- once ht_plates is
    set (true for every HT movement post go-live), that adjusted `load` is
    discarded by _resolve_ht_current_setup, so a LOAD override on an HT slot
    had zero effect. This is the HT-specific counterpart, applied to plates
    directly so it survives.
    """
    if tier_exercise_id is None:
        return plates
    from ..models.enums import OverrideType
    from ..models.program import SlotMovementOverride
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == tier_exercise_id,
        SlotMovementOverride.override_type == OverrideType.LOAD,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is None:
        return plates
    if ov.load_absolute is not None:
        return ov.load_absolute
    if ov.load_delta is not None:
        return plates + ov.load_delta
    return plates


def _is_ht_movement(movement: Movement) -> bool:
    """HT (band-composite) detection — mirrors validator._check_ht_safety."""
    return (movement.lift_category == LiftCategory.HIP_THRUST
            or movement.progression_mode == ProgressionMode.COMPOSITE)


def _resolve_ht_current_setup(state: Optional[MovementState], load: Optional[float]) -> Tuple[float, list]:
    """Read the movement's current (plates, config) from state, with safe defaults:
    plates from ht_plates, else the resolved load, else 0.0; config from
    ht_band_config, else [ht_band_pair_id], else []."""
    if state is not None and state.ht_plates is not None:
        plates = state.ht_plates
    else:
        plates = load if load is not None else 0.0
    if state is not None and state.ht_band_config is not None:
        config = list(state.ht_band_config)
    elif state is not None and state.ht_band_pair_id is not None:
        config = [state.ht_band_pair_id]
    else:
        config = []
    return plates, config


def _resolve_unified_ht_state(
    db: DBSession,
    movement_id: int,
    unified_ht_group: str,
) -> HtProgressionState:
    state = db.exec(
        select(HtProgressionState).where(
            HtProgressionState.movement_id == movement_id,
            HtProgressionState.unified_ht_group == unified_ht_group,
        )
    ).first()
    if state is None:
        raise ValueError(
            "missing HtProgressionState for unified HT slot "
            f"movement_id={movement_id} unified_ht_group={unified_ht_group!r}"
        )
    return state


def _reconcile_ht_performed_floor(db: DBSession, movement_id: int, day_role: str,
                                   cur_plates: float, cur_config: list, by_id: dict) -> float:
    """Floor cur_plates to the last completed same-role felt_peak when the
    logged set used the same band config. Falls through for cold-start,
    mismatch, or no-data cases."""
    prior = db.exec(
        select(WorkoutSession)
        .where(
            WorkoutSession.day_role == day_role,
            WorkoutSession.status == SessionStatus.COMPLETED,
        )
        .order_by(WorkoutSession.date.desc())
    ).first()
    if prior is None:
        return cur_plates

    last_set = db.exec(
        select(SetLog)
        .where(
            SetLog.session_id == prior.id,
            SetLog.movement_id == movement_id,
            SetLog.is_warmup == False,  # noqa: E712
            SetLog.felt_peak.is_not(None),
        )
        .order_by(SetLog.set_index.desc())
    ).first()
    if last_set is None:
        return cur_plates

    logged_config = None
    if last_set.planned_set_id is not None:
        ps = db.get(PlannedSet, last_set.planned_set_id)
        if ps is not None:
            logged_config = resolved_band_config(ps.band_config, ps.band_pair_id)
    if logged_config is None:
        logged_config = resolved_band_config(None, last_set.band_pair_id)
    if logged_config is None or set(logged_config) != set(cur_config):
        return cur_plates

    return ht_performed_floor(cur_plates, cur_config, last_set.felt_peak, by_id)


def _build_exercise(movement: Movement, ex_order: int, ctx: GenerationContext,
                    db: DBSession, prospective: Dict[int, float],
                    day_role: str,
                    is_anchor: bool = False,
                    rep_low: Optional[int] = None, rep_high: Optional[int] = None,
                    rpe_cap: Optional[float] = None,
                    duration_low_seconds: Optional[int] = None,
                    duration_high_seconds: Optional[int] = None,
                    band_inventory: Optional[List[Band]] = None,
                    prospective_ht: Optional[Dict[int, Tuple[float, list]]] = None,
                    prospective_ht_unified: Optional[Dict[Tuple[int, str], Tuple[float, list]]] = None,
                    tier_exercise_id: Optional[int] = None) -> PlannedExercise:
    """Resolve load, compute sets, collect prospective load. Does NOT write DB."""
    state = ctx.movement_states.get(movement.id)
    objective = resolve_objective(movement.objective_override,
                                  ctx.phase_policy.default_objective)
    step, floor = _step_and_floor(movement, db)
    base = resolve_start_load(movement, state, db)
    if base is None:
        # needs-calibration (or bodyweight): assemble the slot structurally with
        # NO target_load — never fabricate a floor.  No prospective load to collect.
        load = None
    else:
        # K2 advance->load bridge: a clean advance last session staged an earned
        # load step on the state (pending_load_delta). Fold it into the base BEFORE
        # rounding/clamping so THIS session prescribes the earned load. commit_session
        # writes the result to current_load and clears the marker (apply-once).
        if state is not None and state.pending_load_delta is not None:
            base = base + state.pending_load_delta
        load = clamp_to_cap(round_to_achievable(base, floor, step), movement.cap)
        # Collect prospective load — caller must NOT write this to MovementState.
        # Captured BEFORE the slot-override adjustment below: a LOAD override is
        # a prescription-only tweak (Option-C) and must never leak into the
        # value that commit_session eventually writes to MovementState.current_load.
        prospective[movement.id] = load
    # Task 1 (note-apply REDESIGN): apply an active LOAD/REPS override for this
    # slot's TierExercise at prescription time — after the prospective load is
    # captured, before it feeds _sets_for_scheme.
    load, rep_low, rep_high = _apply_slot_override(db, tier_exercise_id, load, rep_low, rep_high)
    sets = _sets_for_scheme(movement.scheme, load, ctx,
                            rep_low=rep_low, rep_high=rep_high, rpe_cap=rpe_cap,
                            duration_low_seconds=duration_low_seconds,
                            duration_high_seconds=duration_high_seconds)
    if is_anchor and movement.ramp_eligible and load is not None:
        ramp_sets = [
            PlannedSet(
                set_index=set_index,
                set_role=SetRole.RAMP,
                is_warmup=True,
                # Ramp sets always round UP to a clean 5lb increment, never
                # nearest and never the movement's own working-set step.
                target_load=round_up_to_step(load * pct, floor, 5),
                target_reps_low=reps,
                target_reps_high=reps,
            )
            for set_index, pct, reps in (
                (-3, 0.4, 5),
                (-2, 0.6, 3),
                (-1, 0.8, 2),
            )
        ]
        sets = ramp_sets + sets

    tier_exercise = db.get(TierExercise, tier_exercise_id) if tier_exercise_id is not None else None
    unified_ht_group = (
        tier_exercise.unified_ht_group
        if tier_exercise is not None and tier_exercise.unified_ht_group is not None
        else None
    )
    has_current_ht_setup = (
        unified_ht_group is not None
        or base is not None
        or (state is not None and state.ht_plates is not None)
    )
    if _is_ht_movement(movement) and band_inventory is not None and has_current_ht_setup:
        by_id = {b.id: b for b in band_inventory}
        if unified_ht_group is not None:
            ht_state = _resolve_unified_ht_state(db, movement.id, unified_ht_group)
            cur_plates = ht_state.ht_plates
            cur_config = list(ht_state.ht_band_config or [])
            cur_plates = _reconcile_ht_performed_floor(
                db, movement.id, day_role, cur_plates, cur_config, by_id
            )
            if prospective_ht_unified is not None:
                # Use the clean-gated setup staged on the unified row, if one
                # exists. Otherwise hold at the current unified setup.
                if ht_state.pending_ht_plates is not None:
                    next_plates = ht_state.pending_ht_plates
                    next_config = ht_state.pending_ht_band_config or cur_config
                else:
                    next_plates, next_config = cur_plates, cur_config
                prospective_ht_unified[(movement.id, unified_ht_group)] = (
                    next_plates,
                    list(next_config),
                )
        else:
            cur_plates, cur_config = _resolve_ht_current_setup(state, load)
            if state is not None:
                cur_plates = _reconcile_ht_performed_floor(
                    db, movement.id, day_role, cur_plates, cur_config, by_id
                )
            if prospective_ht is not None:
                # Use the clean-gated setup staged by run_analysis, if one exists.
                # Otherwise hold at the current setup; generation no longer computes
                # unconditional HT advancement.
                if state is not None and state.pending_ht_plates is not None:
                    next_plates = state.pending_ht_plates
                    next_config = state.pending_ht_band_config or cur_config
                else:
                    next_plates, next_config = cur_plates, cur_config
                prospective_ht[movement.id] = (next_plates, list(next_config))
        # A LOAD override on this slot bumps HT's PRESCRIBED plates only (the
        # scalar `load` override above was already discarded by
        # _resolve_ht_current_setup once ht_plates was set -- apply it exactly
        # once, here, not via the scalar path) -- applied AFTER prospective_ht is
        # staged from the un-overridden state.
        prescribed_plates = _apply_ht_load_override(db, tier_exercise_id, cur_plates)
        # Prescribe the CURRENT seeded setup (plus any active override) on the
        # planned sets — no auto-advance at prescription time (2026-07-06 athlete
        # directive: Week 1 shows exactly the seeded setup). Advancement is TIMED
        # at commit, not prescription.
        cur_peak = config_peak(prescribed_plates, cur_config, by_id)
        for ps in sets:
            ps.target_plates = prescribed_plates
            ps.band_config = list(cur_config)
            ps.target_felt_peak = cur_peak
            ps.target_load = None

    ex = PlannedExercise(
        movement_id=movement.id,
        tier_exercise_id=tier_exercise_id,
        order_index=ex_order,
        scheme=movement.scheme,
        objective=objective,
    )
    ex.planned_sets.extend(sets)
    return ex


def planned_sets_in_group_order(
    group: ExerciseGroup,
) -> List[Tuple[PlannedExercise, PlannedSet]]:
    """Return planned sets in the order a group should be trained.

    STRAIGHT and GIANT_SET preserve the existing nested exercise/set order.
    ALTERNATING keeps warmups first, then round-robins non-warmup sets across
    the pair. If set counts differ, remaining sets from the longer exercise
    continue after the paired rounds.
    """
    exercises = sorted(group.exercises, key=lambda e: e.order_index)
    if group.group_type != GroupType.ALT_PAIR:
        return [
            (ex, ps)
            for ex in exercises
            for ps in sorted(ex.planned_sets, key=lambda s: s.set_index)
        ]

    ordered: List[Tuple[PlannedExercise, PlannedSet]] = []
    non_warmup: List[Tuple[PlannedExercise, List[PlannedSet]]] = []
    for ex in exercises:
        sets = sorted(ex.planned_sets, key=lambda s: s.set_index)
        ordered.extend((ex, ps) for ps in sets if ps.is_warmup)
        non_warmup.append((ex, [ps for ps in sets if not ps.is_warmup]))

    max_sets = max((len(sets) for _, sets in non_warmup), default=0)
    for set_pos in range(max_sets):
        for ex, sets in non_warmup:
            if set_pos < len(sets):
                ordered.append((ex, sets[set_pos]))
    return ordered


def _alternating_rounds(group: ExerciseGroup) -> int:
    counts = [
        len([ps for ps in ex.planned_sets if not ps.is_warmup])
        for ex in group.exercises
    ]
    return max(counts, default=1) or 1


def prescribe_swap_sets(new_movement: Movement, day_role: str,
                        tier_exercise_id: Optional[int],
                        rep_low: Optional[int], rep_high: Optional[int],
                        rpe_cap: Optional[float], db: DBSession,
                        duration_low_seconds: Optional[int] = None,
                        duration_high_seconds: Optional[int] = None) -> List[PlannedSet]:
    """Build a fresh 3-set prescription for `new_movement`, matching the exact
    per-set structure (target_load / target_reps / HT plates+bands / etc.)
    _build_exercise produces during full-session generation -- reused here
    for a mid-session swap so one exercise's remaining sets can be
    recomputed without regenerating the whole session.

    Deliberately calls _build_exercise with is_anchor=False: ramp sets are
    always logged first (set_index -3..-1, before any WORKING set), so a
    mid-exercise swap never needs to regenerate them even if the swapped
    slot was originally an anchor.

    Returns transient PlannedSet objects (not persisted) in set_index order
    (0, 1, 2) -- the caller matches them onto the session's existing
    not-yet-logged rows by set_index and copies over the target_* fields;
    it does NOT create or delete PlannedSet rows.
    """
    from .context import resolve_context
    from .skeleton import lay_skeleton

    sk = lay_skeleton(day_role, db)

    def week_keyer(d):
        iso = d.isocalendar()
        return (iso[0], iso[1])

    ctx = resolve_context(day_role, sk, db, week_keyer)
    band_inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                      for bp in db.exec(select(BandPair)).all()]
    ex = _build_exercise(
        new_movement, 0, ctx, db, prospective={}, day_role=day_role,
        is_anchor=False, rep_low=rep_low, rep_high=rep_high, rpe_cap=rpe_cap,
        duration_low_seconds=duration_low_seconds,
        duration_high_seconds=duration_high_seconds,
        band_inventory=band_inventory, prospective_ht={}, prospective_ht_unified={},
        tier_exercise_id=tier_exercise_id,
    )
    return ex.planned_sets


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def assemble(selections: Selections, skeleton: Skeleton,
             ctx: GenerationContext, db: DBSession) -> AssembledSession:
    """Turn the LLM's selections into a full in-memory Session.

    Layout:
      1. Anchor movements → STRAIGHT groups, one per anchor TierExercise,
         except linked pair tiers, which accumulate into one ALTERNATING group.
      2. Adaptive slots → iterated in selections.ordering:
           giant tier  → exercises appended into one GIANT_SET group per source tier (rounds=3)
           pair tier   → exercises appended into one ALTERNATING group per linked pair
           other       → own STRAIGHT group each
      All resulting groups (anchor + giant + straight adaptive) are then sorted
      by the source Tier's TRUE tier_order before order_index is assigned --
      anchors are NOT assumed to always sit first. A trailing anchor tier (e.g.
      a core-work finisher placed after the giant-set tiers) must land last,
      matching its real position in Tier.tier_order. Ties (shouldn't normally
      happen -- each Tier has a unique tier_order) break on construction order.

    Computes prospective_current_loads for every movement but does NOT write
    to MovementState or commit anything.
    """
    movements = {m.id: m for m in db.exec(select(Movement)).all()}
    band_inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                      for bp in db.exec(select(BandPair)).all()]
    session = WorkoutSession(
        date=date.today(),
        day_role=skeleton.day_role,
        phase=ctx.phase,
        status=SessionStatus.PLANNED,
        rationale=selections.rationale,
    )
    prospective: Dict[int, float] = {}
    prospective_ht: Dict[int, Tuple[float, list]] = {}
    prospective_ht_unified: Dict[Tuple[int, str], Tuple[float, list]] = {}

    # Every group built below (anchor STRAIGHT, giant GIANT_SET, adaptive
    # STRAIGHT) is collected here tagged with (tier_order, construction_order)
    # instead of being appended to session.groups / given an order_index
    # immediately. The final order_index assignment sorts this list by the
    # TRUE source-Tier tier_order (falling back to construction order as a
    # stable tie-break) so a trailing anchor tier lands where it actually sits
    # in the day, instead of anchors always being forced first.
    construction_order = 0
    pending_groups: List[Tuple[int, int, ExerciseGroup]] = []
    pair_groups: Dict[str, ExerciseGroup] = {}
    pair_group_rank: Dict[str, Tuple[int, int]] = {}
    pair_group_labels: Dict[str, List[str]] = {}

    def append_pair_exercise(
        pair_key: str,
        movement: Movement,
        tier_order: Optional[int],
        label: Optional[str],
        rest_seconds: Optional[int],
        shoe: Optional[str],
        is_anchor: bool,
        rep_low: Optional[int],
        rep_high: Optional[int],
        rpe_cap: Optional[float],
        duration_low_seconds: Optional[int],
        duration_high_seconds: Optional[int],
        tier_exercise_id: Optional[int],
    ) -> None:
        nonlocal construction_order
        if pair_key not in pair_groups:
            pair_groups[pair_key] = ExerciseGroup(
                order_index=0,  # assigned below, after the true-tier-order sort
                group_type=GroupType.ALT_PAIR,
                rounds=1,
                rest_seconds=rest_seconds,
                label=label,
                shoe=shoe,
            )
            pair_group_rank[pair_key] = (tier_order, construction_order)
            pair_group_labels[pair_key] = [label] if label else []
            construction_order += 1
        else:
            group = pair_groups[pair_key]
            if (
                group.rest_seconds is not None
                and rest_seconds is not None
                and group.rest_seconds != rest_seconds
            ):
                logger.warning(
                    "PAIR tiers for %s have mismatched rest_seconds (%s vs %s); "
                    "using first tier value",
                    pair_key, group.rest_seconds, rest_seconds,
                )
            if label and label not in pair_group_labels[pair_key]:
                pair_group_labels[pair_key].append(label)
                group.label = "/".join(pair_group_labels[pair_key])

        group = pair_groups[pair_key]
        ex = _build_exercise(
            movement, len(group.exercises), ctx, db, prospective,
            day_role=skeleton.day_role, is_anchor=is_anchor,
            rep_low=rep_low, rep_high=rep_high, rpe_cap=rpe_cap,
            duration_low_seconds=duration_low_seconds,
            duration_high_seconds=duration_high_seconds,
            band_inventory=band_inventory, prospective_ht=prospective_ht,
            prospective_ht_unified=prospective_ht_unified,
            tier_exercise_id=tier_exercise_id,
        )
        group.exercises.append(ex)

    # 1) Anchor movements — one STRAIGHT group per anchor TierExercise
    for mid, meta in zip(skeleton.anchor_movement_ids, skeleton.anchor_meta):
        m = movements[mid]
        if meta.pair_key:
            append_pair_exercise(
                meta.pair_key, m, meta.tier_order, meta.tier_label,
                meta.rest_seconds, meta.shoe, True,
                meta.rep_low, meta.rep_high, meta.rpe_cap,
                meta.duration_low_seconds, meta.duration_high_seconds,
                meta.tier_exercise_id,
            )
            continue
        group = ExerciseGroup(
            order_index=0,  # assigned below, after the true-tier-order sort
            group_type=GroupType.STRAIGHT,
            rounds=1,
            rest_seconds=meta.rest_seconds,
            label=meta.tier_label,
            shoe=meta.shoe,
        )
        ex = _build_exercise(m, 0, ctx, db, prospective,
                             day_role=skeleton.day_role, is_anchor=True,
                             rep_low=meta.rep_low, rep_high=meta.rep_high,
                             rpe_cap=meta.rpe_cap,
                             duration_low_seconds=meta.duration_low_seconds,
                             duration_high_seconds=meta.duration_high_seconds,
                             band_inventory=band_inventory, prospective_ht=prospective_ht,
                             prospective_ht_unified=prospective_ht_unified,
                             tier_exercise_id=meta.tier_exercise_id)
        group.exercises.append(ex)
        pending_groups.append((meta.tier_order, construction_order, group))
        construction_order += 1

    # 2) Adaptive layer — model's chosen ordering
    selected = {s.slot_id: s for s in selections.slots}
    slot_map = {s.slot_id: s for s in skeleton.adaptive_slots}

    # One ExerciseGroup per source tier for giant slots; dict preserves first-appearance
    # order (used as the group's construction_order tie-break) so multiple slots
    # of the same tier accumulate into a single group.
    giant_groups: Dict[str, ExerciseGroup] = {}
    giant_group_rank: Dict[str, Tuple[int, int]] = {}  # gk -> (tier_order, construction_order)

    for slot_id in selections.ordering:
        sel = selected.get(slot_id)
        if sel is None:
            continue
        slot = slot_map.get(slot_id)
        if slot is None:
            continue
        m = movements.get(sel.movement_id)
        if m is None:
            continue

        if slot.pair_key:
            append_pair_exercise(
                slot.pair_key, m, slot.tier_order, slot.group_key or None,
                slot.rest_seconds, slot.shoe, False,
                slot.rep_low, slot.rep_high, slot.rpe_cap,
                slot.duration_low_seconds, slot.duration_high_seconds,
                slot.tier_exercise_id,
            )
        elif slot.is_giant_tier:
            # group_key identifies the source tier; fallback to slot_id so that
            # manually-constructed SlotSpecs (empty group_key) still get separate groups.
            gk = slot.group_key if slot.group_key else slot_id
            if gk not in giant_groups:
                giant_groups[gk] = ExerciseGroup(
                    order_index=0,  # assigned below, after the true-tier-order sort
                    group_type=GroupType.GIANT_SET,
                    rounds=3,
                    rest_seconds=slot.rest_seconds,
                    label=slot.group_key or None,
                    shoe=slot.shoe,
                )
                giant_group_rank[gk] = (slot.tier_order, construction_order)
                construction_order += 1
            gg = giant_groups[gk]
            ex = _build_exercise(m, len(gg.exercises), ctx, db, prospective,
                                 day_role=skeleton.day_role,
                                 rep_low=slot.rep_low, rep_high=slot.rep_high,
                                 rpe_cap=slot.rpe_cap,
                                 duration_low_seconds=slot.duration_low_seconds,
                                 duration_high_seconds=slot.duration_high_seconds,
                                 band_inventory=band_inventory, prospective_ht=prospective_ht,
                                 prospective_ht_unified=prospective_ht_unified,
                                 tier_exercise_id=slot.tier_exercise_id)
            gg.exercises.append(ex)
        else:
            # knee / conditioning / accessory → own STRAIGHT group
            group = ExerciseGroup(
                order_index=0,  # assigned below, after the true-tier-order sort
                group_type=GroupType.STRAIGHT,
                rounds=1,
                rest_seconds=slot.rest_seconds,
                label=slot.group_key or None,
                shoe=slot.shoe,
            )
            ex = _build_exercise(m, 0, ctx, db, prospective,
                                 day_role=skeleton.day_role,
                                 rep_low=slot.rep_low, rep_high=slot.rep_high,
                                 rpe_cap=slot.rpe_cap,
                                 duration_low_seconds=slot.duration_low_seconds,
                                 duration_high_seconds=slot.duration_high_seconds,
                                 band_inventory=band_inventory, prospective_ht=prospective_ht,
                                 prospective_ht_unified=prospective_ht_unified,
                                 tier_exercise_id=slot.tier_exercise_id)
            group.exercises.append(ex)
            pending_groups.append((slot.tier_order, construction_order, group))
            construction_order += 1

    for gk, gg in giant_groups.items():
        tier_order, rank = giant_group_rank[gk]
        pending_groups.append((tier_order, rank, gg))

    for pk, pg in pair_groups.items():
        tier_order, rank = pair_group_rank[pk]
        if len(pg.exercises) < 2:
            logger.warning(
                "PAIR group %s assembled with %s exercise(s); treating as STRAIGHT",
                pk, len(pg.exercises),
            )
            pg.group_type = GroupType.STRAIGHT
            pg.rounds = 1
        else:
            pg.rounds = _alternating_rounds(pg)
        pending_groups.append((tier_order, rank, pg))

    # Assign order_index by TRUE tier_order (construction_order as a stable,
    # defensive tie-break -- each Tier normally has a unique tier_order, so
    # ties shouldn't occur in practice).
    pending_groups.sort(key=lambda t: (t[0] if t[0] is not None else float("inf"), t[1]))
    for order, (_, _, g) in enumerate(pending_groups):
        g.order_index = order
        session.groups.append(g)

    program_day_id = _program_day_id_from_skeleton(skeleton, db)
    warmup = build_warmup_payload(db, program_day_id)
    finisher = build_finisher_payload(db, program_day_id)
    if program_day_id is not None:
        session.signature = dict(session.signature or {})
        session.signature["program_day_id"] = program_day_id

    return AssembledSession(session=session, prospective_current_loads=prospective,
                            prospective_ht_setups=prospective_ht,
                            prospective_ht_unified=prospective_ht_unified,
                            warmup=warmup,
                            finisher=finisher)
