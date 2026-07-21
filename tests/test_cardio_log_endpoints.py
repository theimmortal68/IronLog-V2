"""Tests for cardio-log endpoints."""
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.api.app as api_app
from ironlog.api.app import app, get_session
from ironlog.models.library import CardioLog
import ironlog.models  # noqa: F401


def _client():
    app.dependency_overrides.clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override():
        with DbSession(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    return client, engine


def test_post_cardio_log_creates_row_with_nullable_fields():
    client, engine = _client()

    resp = client.post("/cardio-log", json={
        "date": "2026-07-20",
        "duration_minutes": 45,
        "avg_hr": None,
        "modality": "WALK",
        "incline_pct": None,
        "backward_walk_done": False,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] is not None
    assert data["date"] == "2026-07-20"
    assert data["duration_minutes"] == 45
    assert data["avg_hr"] is None
    assert data["modality"] == "WALK"
    assert data["incline_pct"] is None
    assert data["backward_walk_done"] is False
    assert data["created_at"] is not None

    with DbSession(engine) as db:
        rows = db.exec(select(CardioLog)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.date == date(2026, 7, 20)
        assert row.duration_minutes == 45
        assert row.avg_hr is None
        assert row.modality == "WALK"
        assert row.incline_pct is None
        assert row.backward_walk_done is False

    client.close()
    app.dependency_overrides.clear()


def test_get_cardio_log_returns_rows_most_recent_first_with_duplicate_dates():
    client, engine = _client()

    with DbSession(engine) as db:
        older = CardioLog(
            date=date(2026, 7, 18),
            duration_minutes=30,
            avg_hr=118,
            modality="WALK",
        )
        first_same_day = CardioLog(
            date=date(2026, 7, 21),
            duration_minutes=40,
            avg_hr=130,
            modality="TREADMILL",
            incline_pct=4.5,
            backward_walk_done=True,
        )
        second_same_day = CardioLog(
            date=date(2026, 7, 21),
            duration_minutes=20,
            avg_hr=122,
            modality="WALK",
        )
        db.add(older)
        db.add(first_same_day)
        db.add(second_same_day)
        db.commit()
        db.refresh(older)
        db.refresh(first_same_day)
        db.refresh(second_same_day)
        expected_ids = [second_same_day.id, first_same_day.id, older.id]

    resp = client.get("/cardio-log")

    assert resp.status_code == 200
    body = resp.json()
    assert [row["id"] for row in body] == expected_ids
    assert [row["date"] for row in body] == [
        "2026-07-21",
        "2026-07-21",
        "2026-07-18",
    ]

    client.close()
    app.dependency_overrides.clear()


def test_get_cardio_weekly_summary_uses_monday_start_boundary(monkeypatch):
    client, engine = _client()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 21)

    monkeypatch.setattr(api_app, "date", FixedDate)

    def assert_summary(expected_count: int):
        resp = client.get("/cardio-log/weekly-summary")
        assert resp.status_code == 200
        assert resp.json() == {
            "count": expected_count,
            "target": 2,
            "week_start": "2026-07-20",
        }

    assert_summary(0)

    with DbSession(engine) as db:
        db.add(CardioLog(
            date=date(2026, 7, 19),
            duration_minutes=30,
            modality="WALK",
        ))
        db.commit()

    assert_summary(0)

    with DbSession(engine) as db:
        db.add(CardioLog(
            date=date(2026, 7, 20),
            duration_minutes=30,
            modality="WALK",
        ))
        db.commit()

    assert_summary(1)

    with DbSession(engine) as db:
        db.add(CardioLog(
            date=date(2026, 7, 21),
            duration_minutes=35,
            modality="TREADMILL",
        ))
        db.add(CardioLog(
            date=date(2026, 7, 21),
            duration_minutes=25,
            modality="WALK",
        ))
        db.commit()

    assert_summary(3)

    client.close()
    app.dependency_overrides.clear()
