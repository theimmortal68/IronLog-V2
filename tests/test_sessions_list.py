# tests/test_sessions_list.py
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import Session as WorkoutSession
from ironlog.models.enums import SessionStatus
import ironlog.models  # ensure all tables registered


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def _override():
        with DbSession(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _make_completed_session(engine, day_role, iso_date):
    with DbSession(engine) as s:
        ws = WorkoutSession(date=date.fromisoformat(iso_date), day_role=day_role,
                            phase="P1", status=SessionStatus.COMPLETED)
        s.add(ws); s.commit(); s.refresh(ws)
        return ws.id


def _make_planned_session(engine, day_role, iso_date):
    with DbSession(engine) as s:
        ws = WorkoutSession(date=date.fromisoformat(iso_date), day_role=day_role,
                            phase="P1", status=SessionStatus.PLANNED)
        s.add(ws); s.commit(); s.refresh(ws)
        return ws.id


def test_sessions_lists_only_completed_newest_first():
    client, engine = _client()
    a = _make_completed_session(engine, "D1 Upper Push", "2026-07-01")
    b = _make_completed_session(engine, "D5 Lower B", "2026-07-02")
    resp = client.get("/sessions")
    assert resp.status_code == 200
    rows = resp.json()
    ids = [r["id"] for r in rows]
    # newest (highest id) first; only COMPLETED present
    assert ids == sorted(ids, reverse=True)
    assert all(r["status"] == "COMPLETED" for r in rows)
    assert {a, b}.issubset(set(ids))
    app.dependency_overrides.clear()


def test_sessions_excludes_planned():
    client, engine = _client()
    completed_id = _make_completed_session(engine, "D1 Upper Push", "2026-07-01")
    planned_id = _make_planned_session(engine, "D2 Lower A", "2026-07-02")
    resp = client.get("/sessions")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert completed_id in ids
    assert planned_id not in ids
    app.dependency_overrides.clear()
