"""Tests for the goal settings endpoints: GET /goals, POST /goals."""
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session as DbSession, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.library import GoalSettings
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

def test_get_goals_empty():
    client, _ = _client()
    resp = client.get("/goals")
    assert resp.status_code == 200
    assert resp.json() is None

def test_post_goals_creates_row():
    client, engine = _client()

    resp = client.post("/goals", json={
        "target_bodyweight": 200.0,
        "target_bodyweight_tolerance": 1.5,
        "target_body_fat_pct": 15.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_bodyweight"] == 200.0
    assert data["target_bodyweight_tolerance"] == 1.5
    assert data["target_body_fat_pct"] == 15.0
    assert data["target_body_fat_pct_tolerance"] is None
    assert data["updated_at"] is not None

    with DbSession(engine) as db:
        rows = db.exec(select(GoalSettings)).all()
        assert len(rows) == 1

def test_post_goals_first_call_missing_required_weight_field_returns_4xx():
    cases = [
        ({"target_bodyweight": 200.0}, "target_bodyweight_tolerance"),
        ({"target_bodyweight_tolerance": 1.5}, "target_bodyweight"),
    ]
    for payload, missing_field in cases:
        client, _ = _client()
        resp = client.post("/goals", json=payload)
        assert 400 <= resp.status_code < 500
        assert resp.status_code != 500
        assert missing_field in str(resp.json()["detail"])

def test_post_goals_partial_update_preserves_existing_fields_and_get_reflects_it():
    client, _ = _client()

    resp = client.post("/goals", json={
        "target_bodyweight": 200.0,
        "target_bodyweight_tolerance": 1.5,
        "target_body_fat_pct_tolerance": 0.75,
    })
    assert resp.status_code == 200

    resp = client.post("/goals", json={"target_body_fat_pct": 15.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_bodyweight"] == 200.0
    assert data["target_bodyweight_tolerance"] == 1.5
    assert data["target_body_fat_pct"] == 15.0
    assert data["target_body_fat_pct_tolerance"] == 0.75

    resp = client.get("/goals")
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_bodyweight"] == 200.0
    assert data["target_bodyweight_tolerance"] == 1.5
    assert data["target_body_fat_pct"] == 15.0
    assert data["target_body_fat_pct_tolerance"] == 0.75
