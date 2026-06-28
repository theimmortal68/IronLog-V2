# tests/test_submit_endpoint.py
from datetime import date
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import Session, SetLog
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


def _planned_session(engine):
    with DbSession(engine) as s:
        ws = Session(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1",
                     status=SessionStatus.PLANNED)
        s.add(ws); s.commit(); s.refresh(ws)
        return ws.id


def test_submit_writes_setlogs_and_completes():
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": 3, "set_index": 0,
                          "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["set_logs_written"] == 1
    with DbSession(engine) as s:
        assert s.get(Session, sid).status == SessionStatus.COMPLETED
        assert len(s.exec(select(SetLog).where(SetLog.session_id == sid)).all()) == 1
    app.dependency_overrides.clear()


def test_submit_rejects_working_set_without_tap_422_and_writes_nothing():
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": 3, "set_index": 0,
                          "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": None}],   # tapless working set
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 422
    with DbSession(engine) as s:
        assert s.exec(select(SetLog).where(SetLog.session_id == sid)).all() == []
        assert s.get(Session, sid).status == SessionStatus.PLANNED   # untouched
    app.dependency_overrides.clear()


def test_submit_idempotent_lost_ack_retry_writes_nothing_new():
    """The real-world path: submit succeeds, ack lost, client retries."""
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": 3, "set_index": 0,
                          "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r1 = client.post(f"/sessions/{sid}/submit", json=body)
    assert r1.status_code == 200 and r1.json()["already_completed"] is False
    r2 = client.post(f"/sessions/{sid}/submit", json=body)   # lost-ack retry
    assert r2.status_code == 200 and r2.json()["already_completed"] is True
    with DbSession(engine) as s:
        # exactly ONE SetLog — no duplicate from the retry
        assert len(s.exec(select(SetLog).where(SetLog.session_id == sid)).all()) == 1
    app.dependency_overrides.clear()
