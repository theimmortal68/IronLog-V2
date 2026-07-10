"""Tests for the /generate candidate preview (Task 1 — S1).

Verifies:
  1. GenerateResponse.preview mirrors SessionDetailResponse's shape for an
     in-memory (uncommitted) candidate session — provisional int ids stand in
     for the real (None) ids of an unpersisted ExerciseGroup/PlannedExercise/
     PlannedSet graph.
  2. /generate writes NOTHING to the DB (Fork 7c) — no WorkoutSession row is
     created just by generating a candidate; only /approve commits.

The brief's illustrative `client`/`db_session` fixtures don't exist in this
repo — every API test file (test_submit_endpoint.py, test_wizard_resolve_and_
start.py, ...) builds its own TestClient + engine via a local `_client()`
helper and overrides `app.dependency_overrides[get_session]`. This file
follows that same pattern, combined with the gen_db fixture's seeding recipe
(tests/conftest.py: seed.seed() + seed_phase1_program) so /generate has a
real seeded "D1 Upper Push" program and returns a non-exhausted candidate.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import Session as WorkoutSession
import ironlog.models  # noqa: F401 — ensure all tables registered


def _client():
    """Seed a full library + Phase 1 program on a StaticPool in-memory engine,
    then wire /generate's get_session dependency to it.

    Mirrors tests/conftest.py's gen_db fixture (seed.seed() + seed_phase1_
    program) but goes through the API layer: StaticPool + check_same_thread=
    False, as every other API test file in this repo uses, since TestClient
    dispatches requests through a worker thread pool.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    import ironlog.db as db
    db.engine = engine
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = engine
    seed.seed()                                    # 103-movement library + EngineState/PhasePolicy

    from ironlog.generation.program_seed import seed_phase1_program
    with DbSession(engine) as s:
        seed_phase1_program(s)                     # Phase 1 program prior (D1 Upper Push, ...)

    def _override():
        with DbSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def test_generate_returns_preview_matching_session_shape():
    client, _engine = _client()
    resp = client.post("/generate", json={"day_role": "D1 Upper Push"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exhausted"] is False
    preview = body["preview"]
    assert preview is not None
    # Same top-level fields as SessionDetailResponse:
    assert set(preview.keys()) >= {"id", "date", "day_role", "phase", "status", "groups"}
    assert preview["day_role"] == "D1 Upper Push"
    assert len(preview["groups"]) >= 1
    # Provisional ids are present ints (display-only), sets carry targets:
    g0 = preview["groups"][0]
    assert isinstance(g0["id"], int)
    s0 = g0["exercises"][0]["planned_sets"][0]
    assert isinstance(s0["id"], int)
    assert "target_load" in s0 and "target_reps_low" in s0
    exercises = {
        ex["movement_name"]: ex
        for group in preview["groups"]
        for ex in group["exercises"]
    }
    assert exercises["Bench Press [PB]"]["unit_hint"] == "lb"
    assert exercises["Ab Wheel [WHEEL]"]["unit_hint"] is None
    assert exercises["Face-Up Incline Knee Raise"]["unit_hint"] == "assist"
    app.dependency_overrides.clear()


def test_generate_does_not_persist_a_session():
    # /generate must write NOTHING to the DB (Fork 7c).
    client, engine = _client()
    with DbSession(engine) as s:
        before = s.exec(select(WorkoutSession)).all()
    client.post("/generate", json={"day_role": "D1 Upper Push"})
    with DbSession(engine) as s:
        after = s.exec(select(WorkoutSession)).all()
    assert len(after) == len(before)
    app.dependency_overrides.clear()
