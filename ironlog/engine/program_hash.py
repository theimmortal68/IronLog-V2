"""Deterministic hashes for mutable Program definitions.

NO from __future__ import annotations (project-wide constraint).
"""
import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Any, Optional

from sqlalchemy.orm import object_session
from sqlmodel import select

from ironlog.models.program import Program, ProgramDay, Tier, TierExercise


__all__ = [
    "compute_program_prescription_hash",
    "compute_slot_topology_hash",
]


_DAY_COLLECTION_ATTRS = ("program_days", "days")
_TIER_COLLECTION_ATTRS = ("tiers",)
_EXERCISE_COLLECTION_ATTRS = ("tier_exercises", "exercises")


def compute_program_prescription_hash(program: Program) -> str:
    """Hash the program fields that can alter generated training."""
    projection = {
        "days": [
            _project_prescription_day(day, tiers)
            for day, tiers in _ordered_program_graph(program)
        ],
    }
    return _sha256_json(projection)


def compute_slot_topology_hash(program: Program) -> str:
    """Hash only the ordered training/rest day skeleton for weekly slots."""
    projection = {
        "days": [
            {
                "day_index": getattr(day, "day_index", None),
                "is_rest": bool(getattr(day, "is_rest", False)),
            }
            for day, _tiers in _ordered_program_graph(program)
        ],
    }
    return _sha256_json(projection)


def _project_prescription_day(day: ProgramDay, tiers: list[tuple[Tier, list[TierExercise]]]) -> dict[str, Any]:
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
                    {
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
                    for exercise in exercises
                ],
            }
            for tier, exercises in tiers
        ],
    }


def _ordered_program_graph(program: Program) -> list[tuple[ProgramDay, list[tuple[Tier, list[TierExercise]]]]]:
    session = _object_session(program)
    days = sorted(_program_days(program, session), key=_day_sort_key)

    graph = []
    for day in days:
        day_session = _object_session(day) or session
        tiers = sorted(_tiers_for_day(day, day_session), key=_tier_sort_key)
        tier_rows = []
        for tier in tiers:
            tier_session = _object_session(tier) or day_session
            exercises = sorted(_exercises_for_tier(tier, tier_session), key=_exercise_sort_key)
            tier_rows.append((tier, exercises))
        graph.append((day, tier_rows))
    return graph


def _program_days(program: Program, session: Any) -> list[ProgramDay]:
    days = _attached_collection(program, _DAY_COLLECTION_ATTRS)
    if days is not None:
        return days

    program_id = getattr(program, "id", None)
    if session is None or program_id is None:
        raise ValueError(
            "Program must have attached days/program_days or be bound to a Session"
        )
    stmt = select(ProgramDay).where(ProgramDay.program_id == program_id)
    return _exec_all(session, stmt)


def _tiers_for_day(day: ProgramDay, session: Any) -> list[Tier]:
    tiers = _attached_collection(day, _TIER_COLLECTION_ATTRS)
    if tiers is not None:
        return tiers

    day_id = getattr(day, "id", None)
    if session is None or day_id is None:
        raise ValueError("ProgramDay must have attached tiers or be bound to a Session")
    stmt = select(Tier).where(Tier.program_day_id == day_id)
    return _exec_all(session, stmt)


def _exercises_for_tier(tier: Tier, session: Any) -> list[TierExercise]:
    exercises = _attached_collection(tier, _EXERCISE_COLLECTION_ATTRS)
    if exercises is not None:
        return exercises

    tier_id = getattr(tier, "id", None)
    if session is None or tier_id is None:
        raise ValueError("Tier must have attached exercises/tier_exercises or be bound to a Session")
    stmt = select(TierExercise).where(TierExercise.tier_id == tier_id)
    return _exec_all(session, stmt)


def _attached_collection(obj: Any, attr_names: tuple[str, ...]) -> Optional[list[Any]]:
    for attr_name in attr_names:
        if hasattr(obj, attr_name):
            value = getattr(obj, attr_name)
            return [] if value is None else list(value)
    return None


def _object_session(obj: Any) -> Any:
    try:
        return object_session(obj)
    except Exception:
        return None


def _exec_all(session: Any, stmt: Any) -> list[Any]:
    if hasattr(session, "exec"):
        return list(session.exec(stmt).all())
    return list(session.execute(stmt).scalars().all())


def _paired_tier_order(tier: Tier, tiers_by_id: dict[int, Tier]) -> Optional[int]:
    paired_tier_id = getattr(tier, "paired_tier_id", None)
    if paired_tier_id is None:
        return None

    paired_tier = tiers_by_id.get(paired_tier_id)
    if paired_tier is None:
        return None
    return getattr(paired_tier, "tier_order", None)


def _sha256_json(projection: dict[str, Any]) -> str:
    payload = json.dumps(
        _normalize_value(projection),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_value(value[key])
            for key in sorted(value.keys(), key=lambda key: str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def _day_sort_key(day: ProgramDay) -> tuple[int, str, bool]:
    return (
        getattr(day, "day_index", None) or 0,
        getattr(day, "day_role", None) or "",
        bool(getattr(day, "is_rest", False)),
    )


def _tier_sort_key(tier: Tier) -> tuple[int, str, bool, int, bool, int]:
    rest_seconds = getattr(tier, "rest_seconds", None)
    rounds = getattr(tier, "rounds", None)
    return (
        getattr(tier, "tier_order", None) or 0,
        str(_normalize_value(getattr(tier, "tier_kind", None)) or ""),
        rest_seconds is None,
        rest_seconds or 0,
        rounds is None,
        rounds or 0,
    )


def _exercise_sort_key(exercise: TierExercise) -> tuple[int, str, bool, int, str]:
    movement_id = getattr(exercise, "movement_id", None)
    return (
        getattr(exercise, "exercise_order", None) or 0,
        getattr(exercise, "slot_id", None) or "",
        movement_id is None,
        movement_id or 0,
        getattr(exercise, "tier_role", None) or "",
    )
