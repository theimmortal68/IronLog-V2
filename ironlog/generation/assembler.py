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
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session as DBSession, select

from ..engine.band_composite import Band, config_peak, ht_next_setup
from ..engine.loading import clamp_to_cap, round_to_achievable
from ..engine.progression import resolve_objective
from ..models.enums import GroupType, LiftCategory, ProgressionMode, Scheme, SessionStatus, SetRole
from ..models.library import BandPair, Equipment, Movement, MovementState
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
                    prospective_ht: Optional[Dict[int, Tuple[float, list]]] = None) -> PlannedExercise:
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
        load = clamp_to_cap(round_to_achievable(base, floor, step), movement.cap)
        # Collect prospective load — caller must NOT write this to MovementState
        prospective[movement.id] = load
    sets = _sets_for_scheme(movement.scheme, load, ctx,
                            rep_low=rep_low, rep_high=rep_high, rpe_cap=rpe_cap)

    has_current_ht_setup = base is not None or (state is not None and state.ht_plates is not None)
    if _is_ht_movement(movement) and band_inventory is not None and has_current_ht_setup:
        cur_plates, cur_config = _resolve_ht_current_setup(state, load)
        by_id = {b.id: b for b in band_inventory}
        new_plates, new_config = ht_next_setup(cur_plates, cur_config, band_inventory)
        peak = config_peak(new_plates, new_config, by_id)
        for ps in sets:
            ps.target_plates = new_plates
            ps.band_config = list(new_config)
            ps.target_felt_peak = peak
        if prospective_ht is not None:
            prospective_ht[movement.id] = (new_plates, list(new_config))

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
           giant kind  → exercises appended into one GIANT_SET group per source tier (rounds=3)
           knee / other → own STRAIGHT group each

    Computes prospective_current_loads for every movement but does NOT write
    to MovementState or commit anything.
    """
    movements = {m.id: m for m in db.exec(select(Movement)).all()}
    band_inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb)
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
        )
        ex = _build_exercise(m, 0, ctx, db, prospective, is_anchor=True,
                             rep_low=meta.rep_low, rep_high=meta.rep_high,
                             rpe_cap=meta.rpe_cap,
                             band_inventory=band_inventory, prospective_ht=prospective_ht)
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

        if slot.kind == "giant":
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
                )
            gg = giant_groups[gk]
            ex = _build_exercise(m, len(gg.exercises), ctx, db, prospective,
                                 rep_low=slot.rep_low, rep_high=slot.rep_high,
                                 rpe_cap=slot.rpe_cap,
                                 band_inventory=band_inventory, prospective_ht=prospective_ht)
            gg.exercises.append(ex)
        else:
            # knee / conditioning / accessory → own STRAIGHT group
            group = ExerciseGroup(
                order_index=0,  # assigned below
                group_type=GroupType.STRAIGHT,
                rounds=1,
                rest_seconds=slot.rest_seconds,
                label=slot.group_key or None,
            )
            ex = _build_exercise(m, 0, ctx, db, prospective,
                                 rep_low=slot.rep_low, rep_high=slot.rep_high,
                                 rpe_cap=slot.rpe_cap,
                                 band_inventory=band_inventory, prospective_ht=prospective_ht)
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

    return AssembledSession(session=session, prospective_current_loads=prospective,
                            prospective_ht_setups=prospective_ht)
