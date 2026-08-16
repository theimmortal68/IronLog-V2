"""Tests for the assist_unit -> unit_hint fix (2026-08-16).

Bug: every ASSISTED-progression movement serialized the same flat "assist"
unit_hint regardless of the actual assist mechanism (degrees / band count /
cable lb / rep count), so the client hardcoded a "°" (degrees) suffix
for all of them -- wrong for band-count-based movements like D6's "Wide-Grip
Pull-up [TOWER + TUBES]".

Fix: `Movement.assist_unit` (AssistUnit enum) now gets populated for the
movements whose real mechanism is confirmed (seed.py), and
`ironlog.api.app._unit_hint_for` derives a specific unit_hint from it,
falling back to the old generic "assist" for any ASSISTED movement not yet
classified (assist_unit is None) -- preserving current display behavior for
those, not a regression.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, create_engine
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session, _unit_hint_for
from ironlog.models.enums import AssistUnit, ProgressionMode
from ironlog.models.library import Movement


# ---------------------------------------------------------------------------
# Unit tests: _unit_hint_for
# ---------------------------------------------------------------------------

def _mv(progression_mode, assist_unit=None):
    return Movement(name="x", base_name="x", progression_mode=progression_mode,
                     assist_unit=assist_unit)


def test_ladder_mode_returns_lb():
    assert _unit_hint_for(_mv(ProgressionMode.LADDER)) == "lb"


def test_composite_mode_returns_lb():
    assert _unit_hint_for(_mv(ProgressionMode.COMPOSITE)) == "lb"


def test_assisted_degrees_returns_assist_degrees():
    mv = _mv(ProgressionMode.ASSISTED, AssistUnit.DEGREES)
    assert _unit_hint_for(mv) == "assist_degrees"


def test_assisted_tube_count_returns_assist_bands():
    mv = _mv(ProgressionMode.ASSISTED, AssistUnit.TUBE_COUNT)
    assert _unit_hint_for(mv) == "assist_bands"


def test_assisted_cable_lb_returns_assist_lb():
    mv = _mv(ProgressionMode.ASSISTED, AssistUnit.CABLE_LB)
    assert _unit_hint_for(mv) == "assist_lb"


def test_assisted_rep_count_returns_assist_reps():
    mv = _mv(ProgressionMode.ASSISTED, AssistUnit.REP_COUNT)
    assert _unit_hint_for(mv) == "assist_reps"


def test_assisted_unclassified_falls_back_to_generic_assist():
    """assist_unit is None (not yet confirmed) -- must preserve the OLD
    generic "assist" hint, not regress to None or guess a specific unit."""
    mv = _mv(ProgressionMode.ASSISTED, None)
    assert _unit_hint_for(mv) == "assist"


def test_non_load_bearing_mode_returns_none():
    assert _unit_hint_for(_mv(ProgressionMode.PROTOCOL)) is None
    assert _unit_hint_for(_mv(ProgressionMode.CONDITIONING)) is None
    assert _unit_hint_for(_mv(ProgressionMode.FINISHER)) is None
    assert _unit_hint_for(_mv(ProgressionMode.NONE)) is None


# ---------------------------------------------------------------------------
# Integration test: real seeded library through /generate
# ---------------------------------------------------------------------------

def _client():
    """Mirrors tests/test_generate_preview.py's _client() helper: seed a full
    library + Phase 1 program on a fresh StaticPool in-memory engine, wire
    /generate's get_session dependency to it."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    import ironlog.db as db
    db.engine = engine
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = engine
    seed.seed()

    from ironlog.generation.program_seed import seed_phase1_program
    with DbSession(engine) as s:
        seed_phase1_program(s)

    def _override():
        with DbSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _exercises_by_name(preview):
    return {
        ex["movement_name"]: ex
        for group in preview["groups"]
        for ex in group["exercises"]
    }


def test_d6_pullup_serializes_with_assist_bands_hint():
    """D6's "Wide-Grip Pull-up [TOWER + TUBES]" (assist_unit=TUBE_COUNT,
    seed.py) must serialize with unit_hint == "assist_bands" in a real
    generated session -- the exact regression this task fixes (it used to
    come through as the generic "assist", which the client renders as
    degrees)."""
    client, _engine = _client()
    resp = client.post("/generate", json={"day_role": "D6 Weak Points"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exhausted"] is False
    exercises = _exercises_by_name(body["preview"])
    assert "Wide-Grip Pull-up [TOWER + TUBES]" in exercises
    assert exercises["Wide-Grip Pull-up [TOWER + TUBES]"]["unit_hint"] == "assist_bands"
    app.dependency_overrides.clear()


def test_unclassified_assisted_movement_keeps_generic_assist_hint():
    """"Wide-Grip Pull-up [TOWER]" (D1 Upper Push's assisted pull-up slot) is
    explicitly OUT of this task's confirmed-classification scope --
    assist_unit stays None. It must still serialize with the OLD "assist"
    unit_hint -- not a regression, exactly current (pre-fix) display
    behavior, verified via a real generated session rather than assumed."""
    client, _engine = _client()
    resp = client.post("/generate", json={"day_role": "D1 Upper Push"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    exercises = _exercises_by_name(body["preview"])
    assert "Wide-Grip Pull-up [TOWER]" in exercises
    assert exercises["Wide-Grip Pull-up [TOWER]"]["unit_hint"] == "assist"
    app.dependency_overrides.clear()
