"""compute_load_trust — the single shared load-trustworthiness function.

Used by generation's resolver, the wizard-state endpoint, AND the completion
gate, so they cannot disagree. Trust is DERIVED every call from event-facts
(current_load/assist_level, SetLog.performed_at, MovementState.confirmed_at) —
never a stored verdict. NO from __future__ import annotations.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Session, select

from ..models.enums import ProgressionMode
from ..models.library import Movement, MovementState
from ..models.session import SetLog

STALE_AFTER_DAYS = 30


class LoadTrust(str, Enum):
    UNKNOWN = "UNKNOWN"   # no real load → needs-calibration, refuse to prescribe
    STALE = "STALE"       # real load but recency > 30d → prescribe-with-confirm
    FRESH = "FRESH"       # real load, recent → use as-is


@dataclass
class LoadTrustResult:
    trust: LoadTrust
    value: Optional[float]
    load_field: Optional[str]   # "current_load" | "assist_level" | None (bodyweight)


def load_field_for_mode(mode: ProgressionMode) -> Optional[str]:
    if mode in (ProgressionMode.LADDER, ProgressionMode.COMPOSITE):
        return "current_load"
    if mode == ProgressionMode.ASSISTED:
        return "assist_level"
    return None   # PROTOCOL / CONDITIONING / NONE → bodyweight, no load


def _as_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize to naive UTC so naive (project utcnow default) and aware
    datetimes compare without raising. Aware → convert to UTC + drop tzinfo."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _resolve_value(movement, state, db, field):
    """current_load/assist_level present (IS NOT NULL) -> use it; else derived-ratio
    anchor -> start_ratio * anchor.e1rm; else None (UNKNOWN). NO floor fallback."""
    if state is not None:
        v = getattr(state, field)
        if v is not None:          # IS NULL check — assist_level == 0 is a real value
            return v
    if field == "current_load" and movement.start_ratio is not None and movement.derived_from_id is not None:
        anchor = db.exec(select(MovementState).where(
            MovementState.movement_id == movement.derived_from_id)).first()
        if anchor is not None and anchor.e1rm is not None:
            return movement.start_ratio * anchor.e1rm
    return None


def _recency(movement, state, db) -> Optional[datetime]:
    last = db.exec(
        select(SetLog.performed_at)
        .where(SetLog.movement_id == movement.id)
        .where(SetLog.is_warmup == False)            # noqa: E712 — working sets only
        .order_by(SetLog.performed_at.desc())
    ).first()
    confirmed = getattr(state, "confirmed_at", None) if state is not None else None
    candidates = [_as_naive_utc(t) for t in (last, confirmed) if t is not None]
    return max(candidates) if candidates else None


def compute_load_trust(movement: Movement, state: Optional[MovementState],
                       db: Session, as_of: datetime) -> LoadTrustResult:
    field = load_field_for_mode(movement.progression_mode)
    if field is None:
        return LoadTrustResult(LoadTrust.FRESH, None, None)   # bodyweight: always fresh, never asked
    value = _resolve_value(movement, state, db, field)
    if value is None:
        return LoadTrustResult(LoadTrust.UNKNOWN, None, field)
    rec = _recency(movement, state, db)
    as_of_n = _as_naive_utc(as_of)
    if rec is None or (as_of_n - rec) > timedelta(days=STALE_AFTER_DAYS):
        return LoadTrustResult(LoadTrust.STALE, value, field)
    return LoadTrustResult(LoadTrust.FRESH, value, field)
