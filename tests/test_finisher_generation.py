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
from ironlog.generation.assembler import build_finisher_payload
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import FinisherLog, ProgramDay


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
    assert body["preview"]["warmup"]["items"][2] == {
        "name": "jump_rope",
        "seconds": 90,
        "rope": "standard",
        "style": "light_bounce",
    }
    assert body["preview"]["finisher"] == {
        "exercise_name": "kb_swing",
        "duration_minutes": 6,
        "params": {
            "weight_lb": 30,
            "work_seconds_per_minute": 40,
            "rest_seconds_per_minute": 20,
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
    assert body["preview"]["warmup"] is None
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
    assert detail.json()["warmup"]["movement_flow_seconds"] == 90


def test_finisher_log_round_trip_updates_day_scoped_state_and_payload(client_engine):
    client, engine = client_engine
    body = _generate(client, "D1 Upper Push")
    approved = client.post(f"/sessions/{body['candidate_id']}/approve")
    assert approved.status_code == 200, approved.text
    session_id = approved.json()["session_id"]

    with DbSession(engine) as s:
        movement = s.exec(select(Movement).where(Movement.name == "kb_swing")).one()
        program_day = s.exec(
            select(ProgramDay).where(ProgramDay.day_role == "D1 Upper Push")
        ).one()
        assert build_finisher_payload(s, program_day.id)["last_logged_weight_lb"] is None
        s.add(MovementState(
            movement_id=movement.id,
            day_id="D5 Lower B",
            current_load=777.0,
        ))
        s.commit()
        movement_id = movement.id
        program_day_id = program_day.id

    resp = client.post(
        f"/sessions/{session_id}/finisher/log",
        json={
            "movement_id": movement_id,
            "actual_weight_lb": 40.0,
            "notes": "felt crisp",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": resp.json()["id"],
        "movement_id": movement_id,
        "actual_weight_lb": 40.0,
        "actual_resistance_level": None,
    }

    with DbSession(engine) as s:
        log = s.get(FinisherLog, resp.json()["id"])
        assert log.session_id == session_id
        assert log.movement_id == movement_id
        assert log.actual_weight_lb == 40.0
        assert log.actual_resistance_level is None
        assert log.notes == "felt crisp"

        states = {
            state.day_id: state.current_load
            for state in s.exec(
                select(MovementState).where(MovementState.movement_id == movement_id)
            ).all()
        }
        assert states["D1 Upper Push"] == 40.0
        assert states["D5 Lower B"] == 777.0
        assert build_finisher_payload(s, program_day_id)["last_logged_weight_lb"] == 40.0
