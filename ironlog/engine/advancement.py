"""Deterministic Microcycle/Mesocycle advancement state machine.

NO from __future__ import annotations (project-wide constraint).
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from ironlog.config import local_today
from ironlog.engine.program_hash import (
    _sha256_json,
    compute_program_prescription_hash,
)
from ironlog.models.periodization import (
    AdvancementLog,
    MacroPlanningState,
    Macrocycle,
    Mesocycle,
    MesocycleTemplate,
    Microcycle,
    MicrocycleDriftStatus,
    MicrocycleLifecycleStatus,
    MicrocycleSlot,
    MicrocycleSlotResolution,
    MicrocycleSlotResolutionSource,
    MicrocycleSlotType,
    PlanStatus,
)
from ironlog.models.program import Program, ProgramDay


LOGGER = logging.getLogger(__name__)

WAITING_FOR_MICROCYCLE_START = "WAITING_FOR_MICROCYCLE_START"
PROGRAM_DRIFT = "PROGRAM_DRIFT"
AWAITING_NEXT_MESOCYCLE = "AWAITING_NEXT_MESOCYCLE"
INCOMPLETE_MICROCYCLE = "INCOMPLETE_MICROCYCLE"


@dataclass
class Transition:
    entity_type: str
    entity_id: int
    from_state: Optional[str]
    to_state: Optional[str]
    reason: str
    details: Optional[dict] = None


@dataclass
class ReconcileResult:
    transitions: list[Transition] = field(default_factory=list)
    final_microcycle_id: Optional[int] = None
    final_mesocycle_id: Optional[int] = None
    blocked_reason: Optional[str] = None


class InvalidPlanConfigurationError(Exception):
    """Raised when a bound Program cannot produce a valid training Microcycle."""


def ensure_first_microcycle_instantiated(
    db: Session, mesocycle: Mesocycle, reconcile_run_id: Optional[str] = None
) -> Microcycle:
    """Create or verify Mesocycle Microcycle #1 as NOT_STARTED with no slots."""
    return _ensure_microcycle_instantiated(
        db, mesocycle, 1, reconcile_run_id=reconcile_run_id
    )


def mark_microcycle_incomplete(db: Session, microcycle_id: int, reason: str) -> Microcycle:
    """Explicit operator path for terminally blocking an active Microcycle."""
    microcycle = db.get(Microcycle, microcycle_id)
    if microcycle is None:
        raise ValueError(f"Microcycle {microcycle_id} does not exist")

    old_status = _value(microcycle.lifecycle_status)
    microcycle.lifecycle_status = MicrocycleLifecycleStatus.INCOMPLETE
    db.add(microcycle)
    _log_advancement(
        db,
        reconcile_run_id=None,
        entity_type="microcycle",
        entity_id=microcycle.id,
        reason=reason,
        details_json={"from_status": old_status},
    )
    db.commit()
    db.refresh(microcycle)
    return microcycle


def reconcile_current_training_state(db: Session) -> ReconcileResult:
    """Advance training lifecycle state to a fixed point or a named block."""
    today = local_today()
    reconcile_run_id = uuid.uuid4().hex
    result = ReconcileResult()

    _read_current_policy_state(db, today)

    for iteration in range(50):
        pending = _find_pending_microcycle(db)
        if pending is not None:
            changed, blocked_reason = _activate_pending_microcycle(
                db, pending, today, reconcile_run_id, result.transitions
            )
            if blocked_reason is not None:
                result.blocked_reason = blocked_reason
                result.final_microcycle_id = pending.id
                result.final_mesocycle_id = pending.mesocycle_id
                break
            if changed:
                continue

        incomplete = _find_incomplete_microcycle(db)
        if incomplete is not None:
            result.blocked_reason = INCOMPLETE_MICROCYCLE
            result.final_microcycle_id = incomplete.id
            result.final_mesocycle_id = incomplete.mesocycle_id
            break

        active = _find_active_microcycle(db)
        if active is None:
            result.final_microcycle_id, result.final_mesocycle_id = _final_ids(db)
            break

        changed, blocked_reason = _reconcile_active_microcycle(
            db, active, today, reconcile_run_id, result.transitions
        )
        if blocked_reason is not None:
            result.blocked_reason = blocked_reason
            result.final_microcycle_id = active.id
            result.final_mesocycle_id = active.mesocycle_id
            break
        if changed:
            continue

        result.final_microcycle_id = active.id
        result.final_mesocycle_id = active.mesocycle_id
        break
    else:
        LOGGER.error("reconcile_current_training_state hit iteration cap")
        result.final_microcycle_id, result.final_mesocycle_id = _final_ids(db)

    if result.final_microcycle_id is None and result.final_mesocycle_id is None:
        result.final_microcycle_id, result.final_mesocycle_id = _final_ids(db)
    return result


def _read_current_policy_state(db: Session, today):
    """Preserve the read-only orchestration seam for later envelope resolution."""
    from ironlog.generation.context import (
        _active_body_comp_state,
        _active_deload_state,
        _current_recovery_status,
        resolve_current_microcycle,
    )

    current_microcycle = resolve_current_microcycle(db, today)
    _active_body_comp_state(db, today)
    _current_recovery_status(db, today)
    if current_microcycle is not None:
        _active_deload_state(db, current_microcycle)


def _activate_pending_microcycle(
    db: Session,
    microcycle: Microcycle,
    today,
    reconcile_run_id: str,
    transitions: list[Transition],
) -> tuple[bool, Optional[str]]:
    if today < microcycle.planned_start_date:
        return False, WAITING_FOR_MICROCYCLE_START

    # The whole activation body is one all-or-nothing block: MicrocycleSlot rows are
    # staged with db.add() well before the commit at the end, so ANY exception raised
    # in between must roll the session back. Without this, staged slot rows survive in
    # the session and can be flushed later by an unrelated caller's commit, leaving a
    # NOT_STARTED Microcycle that owns slots -- a state _find_pending_microcycle's
    # zero-slot eligibility check can never recover from.
    try:
        mesocycle = _get_mesocycle(db, microcycle.mesocycle_id)
        program = _get_program(db, mesocycle)
        current_hash = compute_program_prescription_hash(program)
        if current_hash != mesocycle.program_prescription_hash:
            db.rollback()
            return False, PROGRAM_DRIFT

        program_days = _program_days_for_snapshot(db, program)
        slot_specs = _slot_specs(microcycle, program_days)
        training_slot_count = sum(
            1
            for slot_spec in slot_specs
            if slot_spec["slot_type"] == MicrocycleSlotType.TRAINING
        )
        if training_slot_count == 0:
            microcycle_id = microcycle.id
            mesocycle_id = mesocycle.id
            program_id = program.id
            db.rollback()
            _log_advancement(
                db,
                reconcile_run_id=reconcile_run_id,
                entity_type="microcycle",
                entity_id=microcycle_id,
                reason="INVALID_PLAN_CONFIGURATION",
                details_json={
                    "mesocycle_id": mesocycle_id,
                    "program_id": program_id,
                    "slot_count": len(slot_specs),
                    "training_slot_count": training_slot_count,
                },
            )
            db.commit()
            raise InvalidPlanConfigurationError(
                f"Program {program_id} produced zero TRAINING slots for Microcycle "
                f"{microcycle_id}"
            )

        # Computed from the exact ordered day snapshot the slots are built from, and
        # BEFORE anything is staged -- so the stored hash can never describe a day
        # ordering different from the rows actually persisted.
        slot_topology_hash = _slot_topology_hash_for_snapshot(program_days)

        for slot_spec in slot_specs:
            db.add(MicrocycleSlot(**slot_spec))

        microcycle.slot_topology_hash = slot_topology_hash

        if _same(mesocycle.status, PlanStatus.PLANNED):
            old_status = _value(mesocycle.status)
            mesocycle.status = PlanStatus.ACTIVE
            mesocycle.actual_start_date = today
            db.add(mesocycle)
            _log_advancement(
                db,
                reconcile_run_id=reconcile_run_id,
                entity_type="mesocycle",
                entity_id=mesocycle.id,
                reason="MESOCYCLE_ADVANCED",
                details_json={"from_status": old_status, "to_status": PlanStatus.ACTIVE.value},
            )
            transitions.append(
                Transition(
                    entity_type="mesocycle",
                    entity_id=mesocycle.id,
                    from_state=old_status,
                    to_state=PlanStatus.ACTIVE.value,
                    reason="MESOCYCLE_ADVANCED",
                )
            )

        old_microcycle_status = _value(microcycle.lifecycle_status)
        microcycle.lifecycle_status = MicrocycleLifecycleStatus.ACTIVE
        microcycle.actual_start_date = today
        db.add(microcycle)
        _log_advancement(
            db,
            reconcile_run_id=reconcile_run_id,
            entity_type="microcycle",
            entity_id=microcycle.id,
            reason="MICROCYCLE_ACTIVATED",
            details_json={
                "from_status": old_microcycle_status,
                "to_status": MicrocycleLifecycleStatus.ACTIVE.value,
                "training_slot_count": training_slot_count,
            },
        )
        transitions.append(
            Transition(
                entity_type="microcycle",
                entity_id=microcycle.id,
                from_state=old_microcycle_status,
                to_state=MicrocycleLifecycleStatus.ACTIVE.value,
                reason="MICROCYCLE_ACTIVATED",
                details={"training_slot_count": training_slot_count},
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True, None


def _slot_topology_hash_for_snapshot(program_days: list[ProgramDay]) -> str:
    """Hash the ordered day skeleton exactly as it was snapshotted into slots.

    The projection MUST stay format-compatible with
    ``program_hash.compute_slot_topology_hash`` -- later consumers (e.g.
    ``acknowledge_program_drift.py``) recompute the hash from the live Program with
    that function and compare it against the value stored here.
    """
    return _sha256_json(
        {
            "days": [
                {
                    "day_index": getattr(day, "day_index", None),
                    "is_rest": bool(getattr(day, "is_rest", False)),
                }
                for day in program_days
            ],
        }
    )


def _reconcile_active_microcycle(
    db: Session,
    microcycle: Microcycle,
    today,
    reconcile_run_id: str,
    transitions: list[Transition],
) -> tuple[bool, Optional[str]]:
    changed = False
    blocked_reason = None
    now = datetime.utcnow()

    try:
        old_drift_days = microcycle.drift_days
        old_drift_status = _value(microcycle.drift_status)
        drift_days = max(0, (today - microcycle.planned_end_date).days)
        drift_status = _drift_status_for_days(drift_days)
        if microcycle.drift_days != drift_days or not _same(microcycle.drift_status, drift_status):
            microcycle.drift_days = drift_days
            microcycle.drift_status = drift_status
            db.add(microcycle)
            changed = True
            transitions.append(
                Transition(
                    entity_type="microcycle",
                    entity_id=microcycle.id,
                    from_state=f"{old_drift_status}:{old_drift_days}",
                    to_state=f"{drift_status.value}:{drift_days}",
                    reason="DRIFT_UPDATED",
                )
            )

        slots = _slots_for_microcycle(db, microcycle.id)
        training_slots = [
            slot for slot in slots if _same(slot.slot_type, MicrocycleSlotType.TRAINING)
        ]
        skipped_day_codes = []
        if drift_days > 4:
            for slot in training_slots:
                if _same(slot.resolution, MicrocycleSlotResolution.PENDING):
                    slot.resolution = MicrocycleSlotResolution.SKIPPED
                    slot.resolution_source = MicrocycleSlotResolutionSource.INFERRED_BOUNDARY
                    slot.resolved_at = now
                    db.add(slot)
                    skipped_day_codes.append(slot.day_code)
            if skipped_day_codes:
                changed = True

        if training_slots and all(
            not _same(slot.resolution, MicrocycleSlotResolution.PENDING)
            for slot in training_slots
        ):
            completion_reason = (
                "DRIFT_INFERRED_SKIP" if skipped_day_codes else "ALL_SESSIONS_RESOLVED"
            )
            old_status = _value(microcycle.lifecycle_status)
            microcycle.lifecycle_status = MicrocycleLifecycleStatus.COMPLETE
            microcycle.actual_completion_date = today
            db.add(microcycle)
            _log_advancement(
                db,
                reconcile_run_id=reconcile_run_id,
                entity_type="microcycle",
                entity_id=microcycle.id,
                reason=completion_reason,
                details_json={
                    "drift_days": drift_days,
                    "skipped_day_codes": skipped_day_codes,
                },
            )
            transitions.append(
                Transition(
                    entity_type="microcycle",
                    entity_id=microcycle.id,
                    from_state=old_status,
                    to_state=MicrocycleLifecycleStatus.COMPLETE.value,
                    reason=completion_reason,
                    details={"skipped_day_codes": skipped_day_codes},
                )
            )
            # Entering this branch always mutates state (COMPLETE + its audit row),
            # so a commit is required regardless of what rollover reports back.
            changed = True
            mesocycle = _get_mesocycle(db, microcycle.mesocycle_id)
            _rollover_changed, blocked_reason = _rollover_completed_microcycle(
                db, mesocycle, microcycle, today, reconcile_run_id, transitions
            )

        if changed:
            db.commit()
    except Exception:
        db.rollback()
        raise

    return changed, blocked_reason


def _rollover_completed_microcycle(
    db: Session,
    mesocycle: Mesocycle,
    microcycle: Microcycle,
    today,
    reconcile_run_id: str,
    transitions: list[Transition],
) -> tuple[bool, Optional[str]]:
    template = _get_template(db, mesocycle)
    if not _is_final_microcycle(db, mesocycle, microcycle, template):
        next_microcycle = _ensure_microcycle_instantiated(
            db, mesocycle, microcycle.ordinal + 1, reconcile_run_id=reconcile_run_id
        )
        transitions.append(
            Transition(
                entity_type="microcycle",
                entity_id=next_microcycle.id,
                from_state=None,
                to_state=MicrocycleLifecycleStatus.NOT_STARTED.value,
                reason="MICROCYCLE_INSTANTIATED",
                details={"ordinal": next_microcycle.ordinal},
            )
        )
        return True, None

    changed = False
    if not _same(mesocycle.status, PlanStatus.COMPLETE):
        old_status = _value(mesocycle.status)
        mesocycle.status = PlanStatus.COMPLETE
        mesocycle.actual_end_date = today
        db.add(mesocycle)
        _log_advancement(
            db,
            reconcile_run_id=reconcile_run_id,
            entity_type="mesocycle",
            entity_id=mesocycle.id,
            reason="MESOCYCLE_COMPLETED",
            details_json={"from_status": old_status, "to_status": PlanStatus.COMPLETE.value},
        )
        transitions.append(
            Transition(
                entity_type="mesocycle",
                entity_id=mesocycle.id,
                from_state=old_status,
                to_state=PlanStatus.COMPLETE.value,
                reason="MESOCYCLE_COMPLETED",
            )
        )
        changed = True

    successor = _successor_mesocycle(db, mesocycle)
    macrocycle = db.get(Macrocycle, mesocycle.macrocycle_id) if mesocycle.macrocycle_id else None
    if successor is not None:
        successor_template = _get_template(db, successor)
        _validate_template_cardinality(successor, successor_template)
        if macrocycle is not None and not _same(macrocycle.planning_state, MacroPlanningState.ACTIVE):
            old_state = _value(macrocycle.planning_state)
            macrocycle.planning_state = MacroPlanningState.ACTIVE
            db.add(macrocycle)
            _log_advancement(
                db,
                reconcile_run_id=reconcile_run_id,
                entity_type="macrocycle",
                entity_id=macrocycle.id,
                reason="SUCCESSOR_MESOCYCLE_FOUND",
                details_json={
                    "from_state": old_state,
                    "to_state": MacroPlanningState.ACTIVE.value,
                    "successor_mesocycle_id": successor.id,
                },
            )
            transitions.append(
                Transition(
                    entity_type="macrocycle",
                    entity_id=macrocycle.id,
                    from_state=old_state,
                    to_state=MacroPlanningState.ACTIVE.value,
                    reason="SUCCESSOR_MESOCYCLE_FOUND",
                )
            )
            changed = True
        first_microcycle = ensure_first_microcycle_instantiated(
            db, successor, reconcile_run_id=reconcile_run_id
        )
        transitions.append(
            Transition(
                entity_type="microcycle",
                entity_id=first_microcycle.id,
                from_state=None,
                to_state=MicrocycleLifecycleStatus.NOT_STARTED.value,
                reason="MICROCYCLE_INSTANTIATED",
                details={"ordinal": first_microcycle.ordinal},
            )
        )
        return True, None

    if macrocycle is not None:
        if not _same(macrocycle.planning_state, MacroPlanningState.AWAITING_NEXT_MESOCYCLE):
            old_state = _value(macrocycle.planning_state)
            macrocycle.planning_state = MacroPlanningState.AWAITING_NEXT_MESOCYCLE
            db.add(macrocycle)
            _log_advancement(
                db,
                reconcile_run_id=reconcile_run_id,
                entity_type="macrocycle",
                entity_id=macrocycle.id,
                reason="PLAN_EXHAUSTED",
                details_json={"mesocycle_id": mesocycle.id},
            )
            transitions.append(
                Transition(
                    entity_type="macrocycle",
                    entity_id=macrocycle.id,
                    from_state=old_state,
                    to_state=MacroPlanningState.AWAITING_NEXT_MESOCYCLE.value,
                    reason="PLAN_EXHAUSTED",
                )
            )
            changed = True
    return changed, AWAITING_NEXT_MESOCYCLE


def _find_pending_microcycle(db: Session) -> Optional[Microcycle]:
    candidates = db.exec(
        select(Microcycle)
        .where(Microcycle.lifecycle_status == MicrocycleLifecycleStatus.NOT_STARTED)
        .order_by(Microcycle.planned_start_date, Microcycle.ordinal, Microcycle.id)
    ).all()
    eligible = [
        microcycle
        for microcycle in candidates
        if _slot_count(db, microcycle.id) == 0
        and _pending_activation_eligible(db, microcycle)
    ]
    if not eligible:
        return None
    if len(eligible) > 1:
        # Ambiguity here is a data-integrity problem, but it must NOT crash: the
        # design's own invariant is that GET /training/plan/current always answers
        # with a blocked_reason rather than a 500. Pick deterministically (earliest
        # planned start, then lowest id) and record the anomaly for an operator.
        ids = [microcycle.id for microcycle in eligible]
        LOGGER.warning(
            "Multiple activation-eligible pending Microcycles %s; "
            "selecting the earliest-dated candidate deterministically",
            ids,
        )
    eligible.sort(key=lambda microcycle: (microcycle.planned_start_date, microcycle.id))
    return eligible[0]


def _pending_activation_eligible(db: Session, microcycle: Microcycle) -> bool:
    mesocycle = _get_mesocycle(db, microcycle.mesocycle_id)
    if _same(mesocycle.status, PlanStatus.COMPLETE) or _same(mesocycle.status, PlanStatus.ABANDONED):
        return False

    if microcycle.ordinal > 1:
        predecessor = db.exec(
            select(Microcycle).where(
                Microcycle.mesocycle_id == microcycle.mesocycle_id,
                Microcycle.ordinal == microcycle.ordinal - 1,
            )
        ).first()
        return predecessor is not None and _same(
            predecessor.lifecycle_status, MicrocycleLifecycleStatus.COMPLETE
        )

    if mesocycle.ordinal in (None, 1) or mesocycle.macrocycle_id is None:
        return True

    predecessor_mesocycle = db.exec(
        select(Mesocycle).where(
            Mesocycle.macrocycle_id == mesocycle.macrocycle_id,
            Mesocycle.ordinal == mesocycle.ordinal - 1,
        )
    ).first()
    return predecessor_mesocycle is not None and _same(
        predecessor_mesocycle.status, PlanStatus.COMPLETE
    )


def _find_active_microcycle(db: Session) -> Optional[Microcycle]:
    return db.exec(
        select(Microcycle)
        .where(Microcycle.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE)
        .order_by(Microcycle.planned_start_date.desc(), Microcycle.ordinal.desc())
    ).first()


def _find_incomplete_microcycle(db: Session) -> Optional[Microcycle]:
    return db.exec(
        select(Microcycle)
        .where(Microcycle.lifecycle_status == MicrocycleLifecycleStatus.INCOMPLETE)
        .order_by(Microcycle.planned_start_date.desc(), Microcycle.ordinal.desc())
    ).first()


def _ensure_microcycle_instantiated(
    db: Session,
    mesocycle: Mesocycle,
    ordinal: int,
    reconcile_run_id: Optional[str] = None,
) -> Microcycle:
    template = _get_template(db, mesocycle)
    expected = _expected_microcycle_values(mesocycle, template, ordinal)
    existing = db.exec(
        select(Microcycle).where(
            Microcycle.mesocycle_id == mesocycle.id,
            Microcycle.ordinal == ordinal,
        )
    ).first()
    if existing is not None:
        _verify_instantiated_microcycle(db, existing, expected)
        return existing

    microcycle = Microcycle(
        mesocycle_id=mesocycle.id,
        ordinal=ordinal,
        planned_start_date=expected["planned_start_date"],
        planned_end_date=expected["planned_end_date"],
        expected_sessions=0,
        lifecycle_status=MicrocycleLifecycleStatus.NOT_STARTED,
        planned_posture=expected["planned_posture"],
    )
    db.add(microcycle)
    db.flush()
    _log_advancement(
        db,
        reconcile_run_id=reconcile_run_id,
        entity_type="microcycle",
        entity_id=microcycle.id,
        reason="MICROCYCLE_INSTANTIATED",
        details_json={
            "mesocycle_id": mesocycle.id,
            "ordinal": ordinal,
            "planned_posture": expected["planned_posture"],
        },
    )
    return microcycle


def _expected_microcycle_values(
    mesocycle: Mesocycle, template: MesocycleTemplate, ordinal: int
) -> dict:
    _validate_template_cardinality(mesocycle, template)
    if ordinal < 1 or ordinal > len(template.postures):
        raise ValueError(
            f"Microcycle ordinal {ordinal} is outside template posture count "
            f"{len(template.postures)} for Mesocycle {mesocycle.id}"
        )
    planned_start_date = mesocycle.planned_start_date + timedelta(days=7 * (ordinal - 1))
    return {
        "planned_start_date": planned_start_date,
        "planned_end_date": planned_start_date + timedelta(days=6),
        "planned_posture": template.postures[ordinal - 1],
    }


def _verify_instantiated_microcycle(
    db: Session, microcycle: Microcycle, expected: dict
) -> None:
    mismatches = []
    if not _same(microcycle.lifecycle_status, MicrocycleLifecycleStatus.NOT_STARTED):
        mismatches.append("lifecycle_status")
    if _slot_count(db, microcycle.id) != 0:
        mismatches.append("slot_count")
    for field_name in ("planned_start_date", "planned_end_date", "planned_posture"):
        if getattr(microcycle, field_name) != expected[field_name]:
            mismatches.append(field_name)
    if mismatches:
        raise ValueError(
            f"Existing Microcycle {microcycle.id} does not match instantiation "
            f"expectations: {', '.join(mismatches)}"
        )


def _validate_template_cardinality(
    mesocycle: Mesocycle, template: MesocycleTemplate
) -> None:
    """Enforce the spec's exact cardinality rule (design doc §5d).

    There is no stored microcycle-count field, but it is derivable from the
    Mesocycle's planned date range: a Mesocycle spans whole 7-day Microcycles, so
    week_count = ((planned_end_date - planned_start_date).days + 1) // 7. A template
    whose posture count does not match that exactly is ambiguous configuration and
    fails rather than silently ignoring (or running short of) postures.
    """
    posture_count = len(template.postures)
    if posture_count == 0:
        raise ValueError(f"MesocycleTemplate {template.id} has no postures")

    span_days = (mesocycle.planned_end_date - mesocycle.planned_start_date).days + 1
    derived_microcycle_count = span_days // 7
    if derived_microcycle_count != posture_count:
        raise ValueError(
            f"MesocycleTemplate {template.id} has {posture_count} postures but "
            f"Mesocycle {mesocycle.id} spans {derived_microcycle_count} microcycle(s) "
            f"({span_days} days); the counts must match exactly"
        )


def _is_final_microcycle(
    db: Session,
    mesocycle: Mesocycle,
    microcycle: Microcycle,
    template: MesocycleTemplate,
) -> bool:
    higher = db.exec(
        select(Microcycle).where(
            Microcycle.mesocycle_id == mesocycle.id,
            Microcycle.ordinal > microcycle.ordinal,
        )
    ).first()
    return higher is None and microcycle.ordinal == len(template.postures)


def _successor_mesocycle(db: Session, mesocycle: Mesocycle) -> Optional[Mesocycle]:
    if mesocycle.macrocycle_id is None or mesocycle.ordinal is None:
        return None
    return db.exec(
        select(Mesocycle).where(
            Mesocycle.macrocycle_id == mesocycle.macrocycle_id,
            Mesocycle.ordinal == mesocycle.ordinal + 1,
            Mesocycle.status == PlanStatus.PLANNED,
        )
    ).first()


def _slot_specs(microcycle: Microcycle, program_days: list[ProgramDay]) -> list[dict]:
    specs = []
    for position, day in enumerate(program_days, start=1):
        slot_type = MicrocycleSlotType.REST if day.is_rest else MicrocycleSlotType.TRAINING
        resolution = (
            MicrocycleSlotResolution.NOT_APPLICABLE
            if slot_type == MicrocycleSlotType.REST
            else MicrocycleSlotResolution.PENDING
        )
        specs.append(
            {
                "microcycle_id": microcycle.id,
                "ordinal": position,
                "day_code": f"D{position}",
                "day_label": day.day_role,
                "planned_date": microcycle.planned_start_date + timedelta(days=position - 1),
                "slot_type": slot_type,
                "resolution": resolution,
                "resolution_source": None,
            }
        )
    return specs


def _program_days_for_snapshot(db: Session, program: Program) -> list[ProgramDay]:
    return list(
        db.exec(
            select(ProgramDay)
            .where(ProgramDay.program_id == program.id)
            .order_by(ProgramDay.day_index, ProgramDay.id)
        ).all()
    )


def _slots_for_microcycle(db: Session, microcycle_id: int) -> list[MicrocycleSlot]:
    return list(
        db.exec(
            select(MicrocycleSlot)
            .where(MicrocycleSlot.microcycle_id == microcycle_id)
            .order_by(MicrocycleSlot.ordinal)
        ).all()
    )


def _slot_count(db: Session, microcycle_id: int) -> int:
    return len(_slots_for_microcycle(db, microcycle_id))


def _drift_status_for_days(drift_days: int) -> MicrocycleDriftStatus:
    if drift_days == 0:
        return MicrocycleDriftStatus.ON_TIME
    if drift_days <= 2:
        return MicrocycleDriftStatus.EXTENDED
    return MicrocycleDriftStatus.DRIFT_FLAGGED


def _get_mesocycle(db: Session, mesocycle_id: int) -> Mesocycle:
    mesocycle = db.get(Mesocycle, mesocycle_id)
    if mesocycle is None:
        raise ValueError(f"Mesocycle {mesocycle_id} does not exist")
    return mesocycle


def _get_template(db: Session, mesocycle: Mesocycle) -> MesocycleTemplate:
    template = db.get(MesocycleTemplate, mesocycle.template_id)
    if template is None:
        raise ValueError(f"MesocycleTemplate {mesocycle.template_id} does not exist")
    return template


def _get_program(db: Session, mesocycle: Mesocycle) -> Program:
    if mesocycle.program_id is None:
        raise ValueError(f"Mesocycle {mesocycle.id} is not bound to a Program")
    program = db.get(Program, mesocycle.program_id)
    if program is None:
        raise ValueError(f"Program {mesocycle.program_id} does not exist")
    return program


def _log_advancement(
    db: Session,
    reconcile_run_id: Optional[str],
    entity_type: str,
    entity_id: int,
    reason: str,
    details_json: Optional[dict] = None,
) -> None:
    db.add(
        AdvancementLog(
            reconcile_run_id=reconcile_run_id,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            details_json=details_json,
        )
    )


def _final_ids(db: Session) -> tuple[Optional[int], Optional[int]]:
    microcycle = _find_active_microcycle(db) or _find_pending_microcycle(db)
    if microcycle is not None:
        return microcycle.id, microcycle.mesocycle_id
    microcycle = db.exec(
        select(Microcycle).order_by(
            Microcycle.planned_start_date.desc(), Microcycle.ordinal.desc()
        )
    ).first()
    if microcycle is not None:
        return microcycle.id, microcycle.mesocycle_id
    return None, None


def _same(actual, expected) -> bool:
    return _value(actual) == _value(expected)


def _value(value):
    return value.value if hasattr(value, "value") else value
