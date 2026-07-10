"""test_finisher_generation.py — generated session finisher API surface.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.api.app as api
import ironlog.models  # noqa: F401 — ensure all tables registered
from ironlog.models.library import Movement, MovementState


@pytest.fixture
def client_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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

    api._candidates.clear()
    api.app.dependency_overrides[api.get_session] = _override
    yield TestClient(api.app), engine
    api.app.dependency_overrides.clear()
    api._candidates.clear()


def _generate(client: TestClient, day_role: str) -> dict:
    resp = client.post("/generate", json={"day_role": day_role})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exhausted"] is False
    assert body["preview"] is not None
    return body


def test_generate_d1_includes_kb_swing_finisher(client_engine):
    client, _engine = client_engine

    body = _generate(client, "D1 Upper Push")

    assert body["scope"] == "main-work-only; warmups/Z2 per program doc, not yet in-app"
    assert body["preview"]["finisher"] == {
        "exercise_name": "kb_swing",
        "duration_minutes": 6,
        "params": {
            "weight_lb": 30,
            "target_reps_per_minute": 15,
            "equipment": ["kettlebell_30"],
        },
        "current_duration_seconds": None,
        "current_rope": None,
    }


def test_generate_rest_day_has_no_finisher(client_engine):
    client, _engine = client_engine

    body = _generate(client, "")

    assert body["preview"]["groups"] == []
    assert body["preview"]["finisher"] is None


def test_generate_d6_includes_live_jump_rope_baseline(client_engine):
    client, _engine = client_engine

    body = _generate(client, "D6 Weak Points")

    finisher = body["preview"]["finisher"]
    assert finisher["exercise_name"] == "jump_rope"
    assert finisher["duration_minutes"] == 6
    assert finisher["current_duration_seconds"] == 35
    assert finisher["current_rope"] == "quarter_lb"


def test_generate_d6_reads_current_jump_rope_state(client_engine):
    client, engine = client_engine
    with DbSession(engine) as s:
        movement = s.exec(select(Movement).where(Movement.name == "jump_rope")).one()
        state = s.exec(
            select(MovementState).where(MovementState.movement_id == movement.id)
        ).one()
        state.current_duration_seconds = 50
        state.current_rope = "half_lb"
        s.add(state)
        s.commit()

    body = _generate(client, "D6 Weak Points")

    finisher = body["preview"]["finisher"]
    assert finisher["current_duration_seconds"] == 50
    assert finisher["current_rope"] == "half_lb"


def test_approved_session_detail_includes_finisher(client_engine):
    client, _engine = client_engine
    body = _generate(client, "D1 Upper Push")

    approved = client.post(f"/sessions/{body['candidate_id']}/approve")
    assert approved.status_code == 200, approved.text
    detail = client.get(f"/sessions/{approved.json()['session_id']}")
    assert detail.status_code == 200, detail.text

    assert detail.json()["finisher"]["exercise_name"] == "kb_swing"
