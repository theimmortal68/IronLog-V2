"""assembler.py — deterministic glue: selections -> Session (Fork 6). No new math.

Turns the LLM's selections into a full in-memory Session (groups → exercises → sets)
with every number computed via existing engine logic.  Computes the prospective
current_load for each movement but does NOT write it — that is committed at approve
(Task 9).

Public API:
  resolve_start_load(movement, state, db) -> float   — Pin 1: single fresh-movement resolver
  assemble(selections, skeleton, ctx, db) -> AssembledSession

NO from __future__ import annotations (project-wide constraint).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from sqlmodel import Session as DBSession, select

from ..engine.loading import clamp_to_cap, current_increment, round_to_achievable
from ..engine.progression import resolve_objective
from ..models.enums import GroupType, Scheme, SessionStatus, SetRole
from ..models.library import Equipment, Movement, MovementState
from ..models.session import ExerciseGroup, PlannedExercise, PlannedSet
from ..models.session import Session as WorkoutSession
from .context import GenerationContext
from .proposer import Selections
from .skeleton import Skeleton


@dataclass
class AssembledSession:
    """The fully assembled in-memory session returned by assemble()."""
    session: WorkoutSession
    prospective_current_loads: Dict[int, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pin 1: single source for a movement's working load
# ---------------------------------------------------------------------------

def resolve_start_load(movement: Movement, state: Optional[MovementState],
                       db: DBSession) -> float:
    """Single source for a movement's working load (Pin 1 — routes to existing
    fields).  If state.current_load is set, use it; else a fresh movement starts
    from start_ratio * anchor_e1rm (ratio-variant) or its load_floor.

    The assembler must NOT contain a duplicate fresh-movement branch — all paths
    route through here.
    """
    if state is not None and state.current_load is not None:
        return state.current_load
    if movement.start_ratio is not None and movement.derived_from_id is not None:
        anchor_state = db.exec(
            select(MovementState).where(
                MovementState.movement_id == movement.derived_from_id)
        ).first()
        if anchor_state is not None and anchor_state.e1rm is not None:
            return movement.start_ratio * anchor_state.e1rm
    return movement.load_floor if movement.load_floor is not None else 0.0


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


def _sets_for_scheme(scheme: Scheme, load: float, ctx: GenerationContext,
                     is_anchor: bool) -> List[PlannedSet]:
    """Map scheme → a concrete list of PlannedSets with reps/RPE from the phase band.

    TOPSET_BACKOFF: TOP set + one BACKOFF at 90 %.
    Everything else: 3 WORKING sets (STRAIGHT / DOUBLE_PROGRESSION / etc.).
    """
    pol = ctx.phase_policy
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
                target_load=round(load * 0.9, 1),
                target_reps_low=5, target_reps_high=8,
                target_rpe=pol.rpe_band_low,
            ),
        ]
    # Default: 3 WORKING sets
    return [
        PlannedSet(
            set_index=i, set_role=SetRole.WORKING,
            target_load=load,
            target_reps_low=8, target_reps_high=12,
            target_rpe=pol.rpe_band_high,
        )
        for i in range(3)
    ]


def _build_exercise(movement: Movement, ex_order: int, ctx: GenerationContext,
                    db: DBSession, prospective: Dict[int, float],
                    is_anchor: bool = False) -> PlannedExercise:
    """Resolve load, compute sets, collect prospective load.  Does NOT write DB."""
    state = ctx.movement_states.get(movement.id)
    objective = resolve_objective(movement.objective_override,
                                  ctx.phase_policy.default_objective)
    step, floor = _step_and_floor(movement, db)
    base = resolve_start_load(movement, state, db)
    load = clamp_to_cap(round_to_achievable(base, floor, step), movement.cap)
    # Collect prospective load — caller must NOT write this to MovementState
    prospective[movement.id] = load
    sets = _sets_for_scheme(movement.scheme, load, ctx, is_anchor)
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
           giant kind  → exercises appended into one shared GIANT_SET group (rounds=3)
           knee / other → own STRAIGHT group each

    Computes prospective_current_loads for every movement but does NOT write
    to MovementState or commit anything.
    """
    movements = {m.id: m for m in db.exec(select(Movement)).all()}
    session = WorkoutSession(
        date=date.today(),
        day_role=skeleton.day_role,
        phase=ctx.phase,
        status=SessionStatus.PLANNED,
        rationale=selections.rationale,
    )
    prospective: Dict[int, float] = {}
    order = 0

    # 1) Anchor movements — STRAIGHT groups, T1 ordering
    for mid in skeleton.anchor_movement_ids:
        m = movements[mid]
        group = ExerciseGroup(
            order_index=order,
            group_type=GroupType.STRAIGHT,
            rounds=1,
        )
        ex = _build_exercise(m, 0, ctx, db, prospective, is_anchor=True)
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
                )
            gg = giant_groups[gk]
            ex = _build_exercise(m, len(gg.exercises), ctx, db, prospective)
            gg.exercises.append(ex)
        else:
            # knee / conditioning / accessory → own STRAIGHT group
            group = ExerciseGroup(
                order_index=0,  # assigned below
                group_type=GroupType.STRAIGHT,
                rounds=1,
            )
            ex = _build_exercise(m, 0, ctx, db, prospective)
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

    return AssembledSession(session=session, prospective_current_loads=prospective)
