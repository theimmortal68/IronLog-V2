"""Tests for the readiness endpoints: GET /readiness/today, POST /readiness, POST /engine-state/confirm-phase."""
from datetime import date, datetime
import json

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session as DbSession
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.library import EngineState, DailyReadiness
from ironlog.models.enums import Phase
import ironlog.models  # noqa: F401

def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def override():
        with DbSession(engine) as session:
            yield session
    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    return client, engine

def test_get_readiness_today_empty():
    client, _ = _client()
    resp = client.get("/readiness/today")
    assert resp.status_code == 200
    assert resp.json() is None

def test_post_readiness_upsert():
    client, engine = _client()
    
    # First submit
    resp = client.post("/readiness", json={"bodyweight": 200.5, "sleep_ok": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bodyweight"] == 200.5
    assert data["sleep_ok"] is True
    assert data["subjective_ok"] is None
    
    # Upsert same day (partial)
    resp = client.post("/readiness", json={"subjective_ok": False, "resting_hr": 55.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bodyweight"] == 200.5   # Untouched
    assert data["sleep_ok"] is True      # Untouched
    assert data["subjective_ok"] is False # Updated
    assert data["resting_hr"] == 55.0    # Updated
    
    with DbSession(engine) as db:
        rows = db.query(DailyReadiness).all()
        assert len(rows) == 1

def test_confirm_phase_success():
    client, engine = _client()
    with DbSession(engine) as db:
        es = EngineState(id=1, current_phase=Phase.STAB, pending_phase_transition="REBUILD")
        db.add(es)
        db.commit()
    
    resp = client.post("/engine-state/confirm-phase", json={"to_phase": "REBUILD"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "confirmed", "current_phase": "REBUILD"}
    
    with DbSession(engine) as db:
        es = db.get(EngineState, 1)
        assert es.current_phase == Phase.REBUILD
        assert es.pending_phase_transition is None

def test_confirm_phase_reject_stale_or_missing():
    client, engine = _client()
    with DbSession(engine) as db:
        es = EngineState(id=1, current_phase=Phase.CUT, pending_phase_transition=None)
        db.add(es)
        db.commit()
    
    resp = client.post("/engine-state/confirm-phase", json={"to_phase": "STAB"})
    assert resp.status_code == 400
    
    with DbSession(engine) as db:
        es = db.get(EngineState, 1)
        es.pending_phase_transition = "REBUILD"
        db.add(es)
        db.commit()
        
    resp = client.post("/engine-state/confirm-phase", json={"to_phase": "STAB"})
    assert resp.status_code == 400
