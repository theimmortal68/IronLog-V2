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
    """A COMPLETED session with a tapped working SetLog (Bench 165x8, ON_TARGET, RPE 8,
    felt_peak 250), one ExerciseSurvey (asymmetry flagged), a session note, and a
    per-exercise note — exercises every field the logs endpoint now returns."""
    from ironlog.models.session import ExerciseSurvey, Note
    with DbSession(engine) as s:
        mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
        s.add(mv); s.commit(); s.refresh(mv)

        ws = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push",
                            phase="P1", status=SessionStatus.COMPLETED)
        s.add(ws); s.commit(); s.refresh(ws)

        s.add(SetLog(session_id=ws.id, movement_id=mv.id, set_index=0,
                     actual_load=165.0, actual_reps=8, rpe_numeric=8.0, felt_peak=250.0,
                     feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False))
        s.add(ExerciseSurvey(session_id=ws.id, movement_id=mv.id,
                             asymmetry_flag=True, technique_flag=False))
        s.add(Note(session_id=ws.id, movement_id=None, text="felt strong"))
        s.add(Note(session_id=ws.id, movement_id=mv.id, text="right side lagging"))
        s.commit()
        return ws.id, mv.id


def test_session_logs_returns_actuals():
    client, engine = _client()
    sid, mid = _make_completed_session_with_logs(engine)
    resp = client.get(f"/sessions/{sid}/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert len(body["logs"]) >= 1
    first = body["logs"][0]
    assert set(first.keys()) == {
        "movement_id", "movement_name", "set_index", "reps", "load", "tap",
        "is_warmup", "rpe_numeric", "felt_peak"}
    assert first["load"] == 165.0 and first["reps"] == 8
    assert first["tap"] == "ON_TARGET"
    assert first["rpe_numeric"] == 8.0
    assert first["felt_peak"] == 250.0
    app.dependency_overrides.clear()


def test_session_logs_returns_surveys_and_notes():
    client, engine = _client()
    sid, mid = _make_completed_session_with_logs(engine)
    body = client.get(f"/sessions/{sid}/logs").json()

    assert len(body["surveys"]) == 1
    sv = body["surveys"][0]
    assert set(sv.keys()) == {
        "movement_id", "movement_name", "asymmetry_flag", "technique_flag", "sticking_point"}
    assert sv["movement_id"] == mid
    assert sv["movement_name"]                    # joined from Movement
    assert sv["asymmetry_flag"] is True
    assert sv["technique_flag"] is False

    notes = body["notes"]
    assert {n["text"] for n in notes} == {"felt strong", "right side lagging"}
    session_note = [n for n in notes if n["movement_id"] is None]
    per_ex = [n for n in notes if n["movement_id"] == mid]
    assert len(session_note) == 1 and session_note[0]["text"] == "felt strong"
    assert len(per_ex) == 1 and per_ex[0]["text"] == "right side lagging"
    app.dependency_overrides.clear()


def test_session_logs_empty_surveys_and_notes_when_none():
    client, engine = _client()
    with DbSession(engine) as s:
        ws = WorkoutSession(date=date(2026, 7, 2), day_role="D2 Lower A",
                            phase="P1", status=SessionStatus.COMPLETED)
        s.add(ws); s.commit(); s.refresh(ws)
        sid = ws.id
    body = client.get(f"/sessions/{sid}/logs").json()
    assert body["surveys"] == []
    assert body["notes"] == []
    app.dependency_overrides.clear()


def test_session_logs_404_for_missing():
    client, engine = _client()
    resp = client.get("/sessions/999999/logs")
    assert resp.status_code == 404
    app.dependency_overrides.clear()
