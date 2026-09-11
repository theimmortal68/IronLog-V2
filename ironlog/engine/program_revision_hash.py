"""Extended hashes for ProgramRevision materialization.

Wider projection than program_hash.py: includes MesoRotation (meso_number-keyed)
and MicrocycleParityRotation rows, grouped under their owning TierExercise/slot.

The existing program_hash.py is NOT modified — its current output is still used
by the live (not-yet-migrated) Mesocycle/Microcycle drift check, and changing it
would silently alter current production drift-detection behavior.

NO from __future__ import annotations (project-wide constraint).
"""
from typing import Any

from sqlalchemy.orm import object_session
from sqlmodel import select

from ironlog.engine.program_hash import (
    _day_sort_key,
    _exercise_sort_key,
    _normalize_value,
    _sha256_json,
    _tier_sort_key,
)
from ironlog.models.program import (
    MesoRotation,
    MicrocycleParityRotation,
    Program,
    ProgramDay,
    Tier,
    TierExercise,
)


__all__ = [
    "compute_revision_prescription_hash",
    "compute_revision_topology_hash",
]


def compute_revision_prescription_hash(program: Program) -> str:
    """Hash every behaviorally-relevant program field, including MesoRotation
    (meso_number-keyed) and MicrocycleParityRotation.

    This is the wider projection used for ProgramRevision's prescription_hash.
    """
    graph = _ordered_revision_graph(program)
    session = _object_session(program)
    projection = {
        "days": [
            _project_revision_day(day, tiers, session)
            for day, tiers in graph
        ],
    }
    return _sha256_json(projection)


def compute_revision_topology_hash(program: Program) -> str:
    """Hash only the ordered training/rest day skeleton for weekly slots.

    Same projection as compute_slot_topology_hash(), implemented independently
    to avoid coupling to the live drift-detector's behavior.
    """
    projection = {
        "days": [
            {
                "day_index": getattr(day, "day_index", None),
                "is_rest": bool(getattr(day, "is_rest", False)),
            }
            for day, _tiers in _ordered_revision_graph(program)
        ],
    }
    return _sha256_json(projection)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _project_revision_day(
    day: ProgramDay,
    tiers: list[tuple[Tier, list[TierExercise]]],
    session: Any,
) -> dict[str, Any]:
    tiers_by_id = {
        tier.id: tier
        for tier, _exercises in tiers
        if getattr(tier, "id", None) is not None
    }
    return {
        "day_index": getattr(day, "day_index", None),
        "day_role": getattr(day, "day_role", None),
        "is_rest": bool(getattr(day, "is_rest", False)),
        "warmup_config": _normalize_value(getattr(day, "warmup_config", None)),
        "tiers": [
            {
                "tier_order": getattr(tier, "tier_order", None),
                "tier_kind": _normalize_value(getattr(tier, "tier_kind", None)),
                "paired_tier_order": _paired_tier_order(tier, tiers_by_id),
                "rest_seconds": getattr(tier, "rest_seconds", None),
                "rounds": getattr(tier, "rounds", None),
                "exercises": [
                    _project_revision_exercise(exercise, session)
                    for exercise in exercises
                ],
            }
            for tier, exercises in tiers
        ],
    }


def _project_revision_exercise(
    exercise: TierExercise,
    session: Any,
) -> dict[str, Any]:
    """Project a single TierExercise, plus its MesoRotation/ParityRotation children."""
    base = {
        "slot_id": getattr(exercise, "slot_id", None),
        "exercise_order": getattr(exercise, "exercise_order", None),
        "movement_id": getattr(exercise, "movement_id", None),
        "tier_role": getattr(exercise, "tier_role", None),
        "pattern": getattr(exercise, "pattern", None),
        "knee_modality": _normalize_value(
            getattr(exercise, "knee_modality", None)
        ),
        "rep_low": getattr(exercise, "rep_low", None),
        "rep_high": getattr(exercise, "rep_high", None),
        "duration_low_seconds": getattr(exercise, "duration_low_seconds", None),
        "duration_high_seconds": getattr(exercise, "duration_high_seconds", None),
        "rpe_cap": getattr(exercise, "rpe_cap", None),
        "scheme": _normalize_value(getattr(exercise, "scheme", None)),
        "unified_ht_group": getattr(exercise, "unified_ht_group", None),
        "derived_from_unified_group": getattr(
            exercise, "derived_from_unified_group", None
        ),
        "derive_ratio": getattr(exercise, "derive_ratio", None),
    }

    # MesoRotation rows (meso_number-keyed only — per design decision #2,
    # mesocycle_id-keyed rows are excluded from the revision).
    exercise_id = getattr(exercise, "id", None)
    meso_rotations = []
    if exercise_id is not None and session is not None:
        mrs = _exec_all(
            session,
            select(MesoRotation)
            .where(
                MesoRotation.tier_exercise_id == exercise_id,
                MesoRotation.mesocycle_id.is_(None),  # type: ignore[union-attr]
            ),
        )
        meso_rotations = sorted(
            [
                {
                    "meso_number": getattr(mr, "meso_number", None),
                    "movement_id": getattr(mr, "movement_id", None),
                    "rep_low": getattr(mr, "rep_low", None),
                    "rep_high": getattr(mr, "rep_high", None),
                }
                for mr in mrs
            ],
            key=lambda d: (d.get("meso_number") or 0, d.get("movement_id") or 0),
        )
    base["meso_rotations"] = meso_rotations

    # MicrocycleParityRotation rows.
    parity_rotations = []
    if exercise_id is not None and session is not None:
        wprs = _exec_all(
            session,
            select(MicrocycleParityRotation).where(
                MicrocycleParityRotation.tier_exercise_id == exercise_id,
            ),
        )
        parity_rotations = sorted(
            [
                {
                    "week_parity": getattr(wpr, "week_parity", None),
                    "movement_id": getattr(wpr, "movement_id", None),
                    "rep_low": getattr(wpr, "rep_low", None),
                    "rep_high": getattr(wpr, "rep_high", None),
                }
                for wpr in wprs
            ],
            key=lambda d: (d.get("week_parity") or "", d.get("movement_id") or 0),
        )
    base["parity_rotations"] = parity_rotations

    return base


def _ordered_revision_graph(
    program: Program,
) -> list[tuple[ProgramDay, list[tuple[Tier, list[TierExercise]]]]]:
    """Walk Program → ProgramDay → Tier → TierExercise, sorted deterministically."""
    session = _object_session(program)
    days = sorted(_program_days(program, session), key=_day_sort_key)

    graph = []
    for day in days:
        day_session = _object_session(day) or session
        tiers = sorted(_tiers_for_day(day, day_session), key=_tier_sort_key)
        tier_rows = []
        for tier in tiers:
            tier_session = _object_session(tier) or day_session
            exercises = sorted(
                _exercises_for_tier(tier, tier_session), key=_exercise_sort_key
            )
            tier_rows.append((tier, exercises))
        graph.append((day, tier_rows))
    return graph


def _program_days(program: Program, session: Any) -> list[ProgramDay]:
    program_id = getattr(program, "id", None)
    if session is None or program_id is None:
        raise ValueError(
            "Program must be bound to a Session with a valid id"
        )
    return _exec_all(
        session,
        select(ProgramDay).where(ProgramDay.program_id == program_id),
    )


def _tiers_for_day(day: ProgramDay, session: Any) -> list[Tier]:
    day_id = getattr(day, "id", None)
    if session is None or day_id is None:
        raise ValueError("ProgramDay must be bound to a Session with a valid id")
    return _exec_all(
        session, select(Tier).where(Tier.program_day_id == day_id)
    )


def _exercises_for_tier(tier: Tier, session: Any) -> list[TierExercise]:
    tier_id = getattr(tier, "id", None)
    if session is None or tier_id is None:
        raise ValueError("Tier must be bound to a Session with a valid id")
    return _exec_all(
        session, select(TierExercise).where(TierExercise.tier_id == tier_id)
    )


def _paired_tier_order(
    tier: Tier, tiers_by_id: dict[int, Tier]
) -> int | None:
    paired_tier_id = getattr(tier, "paired_tier_id", None)
    if paired_tier_id is None:
        return None
    paired_tier = tiers_by_id.get(paired_tier_id)
    if paired_tier is None:
        return None
    return getattr(paired_tier, "tier_order", None)


def _object_session(obj: Any) -> Any:
    try:
        return object_session(obj)
    except Exception:
        return None


def _exec_all(session: Any, stmt: Any) -> list[Any]:
    if hasattr(session, "exec"):
        return list(session.exec(stmt).all())
    return list(session.execute(stmt).scalars().all())
