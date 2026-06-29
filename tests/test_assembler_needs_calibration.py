"""test_assembler_needs_calibration.py — Task 3 (wizard): generation flags
needs-calibration for unconfigured loads instead of silently flooring.

The §7.4 gate: an unconfigured LADDER movement (load_floor set, NO MovementState)
must come back needs-calibration (resolve_start_load -> None / target_load None),
NOT its equipment/movement floor.  "Bench must NOT come back 45."

resolve_start_load is now a thin wrapper over compute_load_trust:
  FRESH / STALE -> real load (result.value)
  UNKNOWN       -> None (needs-calibration) — NEVER a floor.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime, timedelta

from sqlmodel import Session as DbSession, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from ironlog.generation.assembler import resolve_start_load
from ironlog.models.enums import ProgressionMode
from ironlog.models.library import Movement, MovementState
import ironlog.models  # noqa: F401 — ensure all tables are registered


def _db():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


def _bench(s):
    m = Movement(name="Bench Press [BB]", base_name="Bench Press",
                 progression_mode=ProgressionMode.LADDER, load_floor=45.0)
    s.add(m)
    s.commit()
    s.refresh(m)
    return m


def test_unconfigured_ladder_movement_is_needs_calibration_not_floor():
    """THE §7.4 gate — Bench (load_floor=45), no MovementState → resolve_start_load
    returns None (needs-calibration), NEVER the 45 floor."""
    e = _db()
    with DbSession(e) as s:
        bench = _bench(s)
        result = resolve_start_load(bench, None, s)
        assert result is None, (
            "an unconfigured movement must be needs-calibration (None), "
            "not silently resolved to a floor"
        )
        assert result != 45.0, "Bench must NOT come back 45 (the floor fallback is gone)"


def test_configured_fresh_movement_returns_real_load_not_floor():
    """A movement WITH a real MovementState.current_load resolves to that load
    (FRESH -> result.value), not None and not the floor."""
    e = _db()
    with DbSession(e) as s:
        bench = _bench(s)
        st = MovementState(movement_id=bench.id, current_load=185.0,
                           confirmed_at=datetime.utcnow() - timedelta(days=2))
        s.add(st)
        s.commit()
        assert resolve_start_load(bench, st, s) == 185.0


def test_stale_movement_still_returns_real_load():
    """A movement with a real load but old recency is STALE — resolve still returns
    the real value (prescribe-with-confirm), never a floor and never None."""
    e = _db()
    with DbSession(e) as s:
        bench = _bench(s)
        st = MovementState(movement_id=bench.id, current_load=185.0,
                           confirmed_at=datetime.utcnow() - timedelta(days=400))
        s.add(st)
        s.commit()
        assert resolve_start_load(bench, st, s) == 185.0
