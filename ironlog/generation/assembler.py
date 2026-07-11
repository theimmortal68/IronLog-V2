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
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session as DBSession, select

from ..engine.band_composite import Band, config_peak, ht_next_setup
from ..engine.loading import clamp_to_cap, round_to_achievable
from ..engine.progression import resolve_objective
from ..models.enums import (
    GroupType, LiftCategory, ProgressionMode, ProgressionRule, Scheme,
    SessionStatus, SetRole,
)
from ..models.library import BandPair, EngineState, Equipment, Movement, MovementState
from ..models.program import DayFinisher, ProgramDay, Tier, TierExercise
from ..models.session import ExerciseGroup, PlannedExercise, PlannedSet
from ..models.session import Session as WorkoutSession
from .context import GenerationContext
from .load_trust import LoadTrust, compute_load_trust
from .proposer import Selections
from .skeleton import Skeleton


@dataclass
class AssembledSession:
    """The fully assembled in-memory session returned by assemble()."""
    session: WorkoutSession
    prospective_current_loads: Dict[int, float] = field(default_factory=dict)
    # HT (band-composite) prospective setup — movement_id -> (plates, config).
    # Mirrors prospective_current_loads: computed here, written only by
    # commit_session at approval (Option-C two-writer boundary).
    prospective_ht_setups: Dict[int, Tuple[float, list]] = field(default_factory=dict)
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

    return {
        "exercise_name": movement.name if movement is not None else "",
        "duration_minutes": finisher.duration_minutes,
        "params": dict(finisher.params or {}),
        "current_duration_seconds": current_duration_seconds,
        "current_rope": current_rope,
    }


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
                     rpe_cap: Optional[float] = None) -> List[PlannedSet]:
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
    if scheme == Scheme.TOPSET_BACKOFF:
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
    lo = rep_low if rep_low is not None else 8
    hi = rep_high if rep_high is not None else 12
    rpe = rpe_cap if rpe_cap is not None else (
        pol.rpe_band_high if pol.rpe_band_high is not None else 8.0
    )
    return [
        PlannedSet(
            set_index=i, set_role=SetRole.WORKING,
            target_load=load,
            target_reps_low=lo, target_reps_high=hi,
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


def _build_exercise(movement: Movement, ex_order: int, ctx: GenerationContext,
                    db: DBSession, prospective: Dict[int, float],
                    is_anchor: bool = False,
                    rep_low: Optional[int] = None, rep_high: Optional[int] = None,
                    rpe_cap: Optional[float] = None,
                    band_inventory: Optional[List[Band]] = None,
                    prospective_ht: Optional[Dict[int, Tuple[float, list]]] = None,
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
                            rep_low=rep_low, rep_high=rep_high, rpe_cap=rpe_cap)
    if is_anchor and movement.ramp_eligible and load is not None:
        ramp_sets = [
            PlannedSet(
                set_index=set_index,
                set_role=SetRole.RAMP,
                is_warmup=True,
                target_load=round_to_achievable(load * pct, floor, step),
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

    has_current_ht_setup = base is not None or (state is not None and state.ht_plates is not None)
    if _is_ht_movement(movement) and band_inventory is not None and has_current_ht_setup:
        cur_plates, cur_config = _resolve_ht_current_setup(state, load)
        by_id = {b.id: b for b in band_inventory}
        if prospective_ht is not None:
            # Stage the NEXT setup from the REAL (pre-override) current state, so
            # commit_session advances MovementState AFTER this session is logged —
            # session N prescribes current, commit moves the state to next, session
            # N+1 prescribes that next, and so on. A LOAD override is a
            # prescription-only tweak (Option-C, mirrors _apply_slot_override's own
            # prospective-captured-before-override pattern above) and must NEVER
            # leak into the trajectory commit_session persists — applying it here
            # would compound the override into ht_plates every regeneration cycle,
            # since an active override has no auto-expiry.
            next_plates, next_config = ht_next_setup(cur_plates, cur_config, band_inventory)
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
        order_index=ex_order,
        scheme=movement.scheme,
        objective=objective,
    )
    ex.planned_sets.extend(sets)
    return ex


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def assemble(selections: Selections, skeleton: Skeleton,
             ctx: GenerationContext, db: DBSession) -> AssembledSession:
    """Turn the LLM's selections into a full in-memory Session.

    Layout:
      1. Anchor movements → STRAIGHT groups in skeleton order (T1 first).
      2. Adaptive slots → iterated in selections.ordering:
           giant tier  → exercises appended into one GIANT_SET group per source tier (rounds=3)
           other       → own STRAIGHT group each

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
    order = 0

    # 1) Anchor movements — STRAIGHT groups, T1 ordering
    for mid, meta in zip(skeleton.anchor_movement_ids, skeleton.anchor_meta):
        m = movements[mid]
        group = ExerciseGroup(
            order_index=order,
            group_type=GroupType.STRAIGHT,
            rounds=1,
            rest_seconds=meta.rest_seconds,
            label=meta.tier_label,
            shoe=meta.shoe,
        )
        ex = _build_exercise(m, 0, ctx, db, prospective, is_anchor=True,
                             rep_low=meta.rep_low, rep_high=meta.rep_high,
                             rpe_cap=meta.rpe_cap,
                             band_inventory=band_inventory, prospective_ht=prospective_ht,
                             tier_exercise_id=meta.tier_exercise_id)
        group.exercises.append(ex)
        session.groups.append(group)
        order += 1

    # 2) Adaptive layer — model's chosen ordering
    selected = {s.slot_id: s for s in selections.slots}
    slot_map = {s.slot_id: s for s in skeleton.adaptive_slots}

    # One ExerciseGroup per source tier for giant slots; dict preserves first-appearance
    # order so the tier groups are added to the session in the proposer's intended order.
    giant_groups: Dict[str, ExerciseGroup] = {}
    # Non-giant (knee / accessory) adaptive groups — collected and appended after giants.
    straight_groups: List[ExerciseGroup] = []

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

        if slot.is_giant_tier:
            # group_key identifies the source tier; fallback to slot_id so that
            # manually-constructed SlotSpecs (empty group_key) still get separate groups.
            gk = slot.group_key if slot.group_key else slot_id
            if gk not in giant_groups:
                giant_groups[gk] = ExerciseGroup(
                    order_index=0,  # assigned below after all slots are processed
                    group_type=GroupType.GIANT_SET,
                    rounds=3,
                    rest_seconds=slot.rest_seconds,
                    label=slot.group_key or None,
                    shoe=slot.shoe,
                )
            gg = giant_groups[gk]
            ex = _build_exercise(m, len(gg.exercises), ctx, db, prospective,
                                 rep_low=slot.rep_low, rep_high=slot.rep_high,
                                 rpe_cap=slot.rpe_cap,
                                 band_inventory=band_inventory, prospective_ht=prospective_ht,
                                 tier_exercise_id=slot.tier_exercise_id)
            gg.exercises.append(ex)
        else:
            # knee / conditioning / accessory → own STRAIGHT group
            group = ExerciseGroup(
                order_index=0,  # assigned below
                group_type=GroupType.STRAIGHT,
                rounds=1,
                rest_seconds=slot.rest_seconds,
                label=slot.group_key or None,
                shoe=slot.shoe,
            )
            ex = _build_exercise(m, 0, ctx, db, prospective,
                                 rep_low=slot.rep_low, rep_high=slot.rep_high,
                                 rpe_cap=slot.rpe_cap,
                                 band_inventory=band_inventory, prospective_ht=prospective_ht,
                                 tier_exercise_id=slot.tier_exercise_id)
            group.exercises.append(ex)
            straight_groups.append(group)

    # Assign clean sequential order_index:
    #   anchors:            0 .. len(anchor_movement_ids)-1   (already set above)
    #   giant tier groups:  next in first-appearance order
    #   knee/accessory:     after all giant groups
    for gg in giant_groups.values():
        gg.order_index = order
        order += 1
        session.groups.append(gg)

    for g in straight_groups:
        g.order_index = order
        order += 1
        session.groups.append(g)

    program_day_id = _program_day_id_from_skeleton(skeleton, db)
    finisher = build_finisher_payload(db, program_day_id)
    if program_day_id is not None:
        session.signature = dict(session.signature or {})
        session.signature["program_day_id"] = program_day_id

    return AssembledSession(session=session, prospective_current_loads=prospective,
                            prospective_ht_setups=prospective_ht,
                            finisher=finisher)
