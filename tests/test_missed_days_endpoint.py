"""Tests for missed-day endpoints."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.program import MissedDayRecord, Program, ProgramDay
import ironlog.models  # noqa: F401


@pytest.fixture
def client_and_engine():
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
    try:
        yield client, engine
    finally:
        client.close()
        app.dependency_overrides.clear()


def _seed_program_day(db: DbSession, program_day_id: int = 1, day_role: str = "D1 Upper Push"):
    db.add(Program(id=1, name="Phase 1", phase="CUT", duration_weeks=8))
    db.add(ProgramDay(
        id=program_day_id,
        program_id=1,
        day_index=1,
        day_role=day_role,
        is_rest=False,
    ))


def _add_missed_day_record(
    db: DbSession,
    program_day_id: int = 1,
    status: str = "PENDING",
    week_start_date: date = date(2026, 7, 13),
) -> MissedDayRecord:
    record = MissedDayRecord(
        program_day_id=program_day_id,
        week_start_date=week_start_date,
        detected_at=datetime(2026, 7, 14, 6, 0, 0),
        status=status,
    )
    db.add(record)
    return record


def test_get_missed_days_empty_list(client_and_engine):
    client, _ = client_and_engine

    resp = client.get("/missed-days")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_missed_days_includes_pending_record_with_joined_day_role(client_and_engine):
    client, engine = client_and_engine
    with DbSession(engine) as db:
        _seed_program_day(db, day_role="D2 Lower A")
        record = _add_missed_day_record(db)
        db.commit()
        db.refresh(record)
        record_id = record.id

    resp = client.get("/missed-days")

    assert resp.status_code == 200
    assert resp.json() == [{
        "id": record_id,
        "program_day_id": 1,
        "day_role": "D2 Lower A",
        "week_start_date": "2026-07-13",
        "detected_at": "2026-07-14T06:00:00",
        "status": "PENDING",
    }]


def test_get_missed_days_excludes_acknowledged_and_resolved_records(client_and_engine):
    client, engine = client_and_engine
    with DbSession(engine) as db:
        _seed_program_day(db)
        _add_missed_day_record(db, status="ACKNOWLEDGED")
        _add_missed_day_record(db, status="RESOLVED")
        db.commit()

    resp = client.get("/missed-days")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_missed_days_skips_record_with_missing_program_day(client_and_engine):
    client, engine = client_and_engine
    with DbSession(engine) as db:
        _seed_program_day(db)
        valid = _add_missed_day_record(db)
        _add_missed_day_record(db, program_day_id=999)
        db.commit()
        db.refresh(valid)
        valid_id = valid.id

    resp = client.get("/missed-days")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == valid_id
    assert body[0]["day_role"] == "D1 Upper Push"


def test_acknowledge_and_reschedule_mutate_status_and_get_reflects_open_records(client_and_engine):
    client, engine = client_and_engine
    with DbSession(engine) as db:
        _seed_program_day(db)
        acknowledged = _add_missed_day_record(db)
        rescheduled = _add_missed_day_record(
            db,
            week_start_date=date(2026, 7, 20),
        )
        db.commit()
        db.refresh(acknowledged)
        db.refresh(rescheduled)
        acknowledged_id = acknowledged.id
        rescheduled_id = rescheduled.id

    ack_resp = client.post(f"/missed-days/{acknowledged_id}/acknowledge")
    reschedule_resp = client.post(f"/missed-days/{rescheduled_id}/reschedule")

    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "ACKNOWLEDGED"
    assert reschedule_resp.status_code == 200
    assert reschedule_resp.json()["status"] == "RESCHEDULED"

    with DbSession(engine) as db:
        assert db.get(MissedDayRecord, acknowledged_id).status == "ACKNOWLEDGED"
        assert db.get(MissedDayRecord, rescheduled_id).status == "RESCHEDULED"

    get_resp = client.get("/missed-days")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert [row["id"] for row in body] == [rescheduled_id]
    assert body[0]["status"] == "RESCHEDULED"


@pytest.mark.parametrize("action", ["acknowledge", "reschedule"])
def test_acting_on_nonexistent_record_returns_404(client_and_engine, action):
    client, _ = client_and_engine

    resp = client.post(f"/missed-days/999/{action}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "missed day record not found"


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        ("acknowledge", "ACKNOWLEDGED"),
        ("reschedule", "RESCHEDULED"),
    ],
)
def test_acting_on_resolved_record_succeeds(client_and_engine, action, expected_status):
    client, engine = client_and_engine
    with DbSession(engine) as db:
        _seed_program_day(db)
        record = _add_missed_day_record(db, status="RESOLVED")
        db.commit()
        db.refresh(record)
        record_id = record.id

    resp = client.post(f"/missed-days/{record_id}/{action}")

    assert resp.status_code == 200
    assert resp.json()["status"] == expected_status
