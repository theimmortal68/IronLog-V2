# tests/test_session_logs.py
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import Session as WorkoutSession, SetLog
from ironlog.models.enums import FeedbackTap, SessionStatus
from ironlog.models.library import Movement
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


def _make_completed_session_with_logs(engine):
    """A COMPLETED session with one tapped working SetLog on a real Movement
    (Bench 165x8, ON_TARGET) — mirrors the capture tests' setup pattern."""
    with DbSession(engine) as s:
        mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
        s.add(mv); s.commit(); s.refresh(mv)

        ws = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push",
                            phase="P1", status=SessionStatus.COMPLETED)
        s.add(ws); s.commit(); s.refresh(ws)

        s.add(SetLog(session_id=ws.id, movement_id=mv.id, set_index=0,
                     actual_load=165.0, actual_reps=8,
                     feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False))
        s.commit()
        return ws.id


def test_session_logs_returns_actuals():
    client, engine = _client()
    sid = _make_completed_session_with_logs(engine)
    resp = client.get(f"/sessions/{sid}/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert len(body["logs"]) >= 1
    first = body["logs"][0]
    assert set(first.keys()) == {
        "movement_id", "movement_name", "set_index", "reps", "load", "tap", "is_warmup"}
    assert first["movement_name"]  # joined from Movement
    assert first["load"] == 165.0
    assert first["reps"] == 8
    assert first["tap"] == "ON_TARGET"
    app.dependency_overrides.clear()


def test_session_logs_404_for_missing():
    client, engine = _client()
    resp = client.get("/sessions/999999/logs")
    assert resp.status_code == 404
    app.dependency_overrides.clear()
