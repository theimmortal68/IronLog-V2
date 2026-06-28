# tests/test_session_read_endpoints.py
from datetime import date
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import (Session, ExerciseGroup, PlannedExercise, PlannedSet)
from ironlog.models.enums import SessionStatus, GroupType, Scheme, Objective, SetRole
from ironlog.models.library import Movement
import ironlog.models


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def _override():
        with DbSession(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _full_session(engine, status=SessionStatus.PLANNED):
    with DbSession(engine) as s:
        mv = s.exec(select(Movement).where(Movement.name == "Bench Press [PB]")).first()
        if mv is None:
            mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
            s.add(mv); s.commit()
        s.refresh(mv)
        ws = Session(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1", status=status)
        s.add(ws); s.commit(); s.refresh(ws)
        g = ExerciseGroup(session_id=ws.id, order_index=0, group_type=GroupType.STRAIGHT, rounds=1)
        s.add(g); s.commit(); s.refresh(g)
        pe = PlannedExercise(group_id=g.id, movement_id=mv.id, order_index=0,
                             scheme=Scheme.TOPSET_BACKOFF, objective=Objective.PROGRESS)
        s.add(pe); s.commit(); s.refresh(pe)
        s.add(PlannedSet(planned_exercise_id=pe.id, set_index=0, set_role=SetRole.TOP,
                         target_load=100.0, target_reps_low=5, target_reps_high=8))
        s.commit()
        return ws.id


def test_get_session_returns_full_graph_with_movement_name():
    client, engine = _client()
    sid = _full_session(engine)
    r = client.get(f"/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["day_role"] == "D1 Upper Push"
    ex = body["groups"][0]["exercises"][0]
    assert ex["movement_name"] == "Bench Press [PB]"
    assert ex["planned_sets"][0]["set_role"] == "TOP"
    app.dependency_overrides.clear()


def test_get_session_404_when_missing():
    client, engine = _client()
    assert client.get("/sessions/999").status_code == 404
    app.dependency_overrides.clear()


def test_today_returns_null_when_no_planned_session():
    client, engine = _client()
    r = client.get("/sessions/today")
    assert r.status_code == 200 and r.json() is None
    app.dependency_overrides.clear()


def test_today_returns_most_recent_planned_when_multiple():
    client, engine = _client()
    first = _full_session(engine)
    second = _full_session(engine)
    r = client.get("/sessions/today")
    assert r.status_code == 200
    assert r.json()["id"] == max(first, second)
    app.dependency_overrides.clear()
