"""Tests for the weak-point assessment endpoint: GET /weak-points."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session as DbSession, create_engine
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models import Movement, MovementWeaknessSignal
from ironlog.models.enums import Muscle
from ironlog.models.session import Session as IronSession
import ironlog.models  # noqa: F401


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override():
        with DbSession(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    return client, engine


def _seed_session(db: DbSession, session_id: int):
    db.add(IronSession(
        id=session_id,
        date=date(2026, 7, 19) + timedelta(days=session_id),
        day_role=f"Day {session_id}",
        phase="CUT",
    ))


def test_get_weak_points_empty():
    client, _ = _client()

    resp = client.get("/weak-points")

    assert resp.status_code == 200
    assert resp.json() == {"muscle_groups": [], "movements": []}


def test_get_weak_points_rolls_up_latest_assessed_movements_by_primary_muscle():
    client, engine = _client()
    computed_at = datetime(2026, 7, 19, 12, 0, 0)

    with DbSession(engine) as db:
        _seed_session(db, 1)
        db.add_all([
            Movement(id=1, name="Back Squat [PB]", base_name="Back Squat", primary_muscle=Muscle.QUADS),
            Movement(id=2, name="Leg Extension", base_name="Leg Extension", primary_muscle=Muscle.QUADS),
            Movement(id=3, name="Lat Pulldown", base_name="Lat Pulldown", primary_muscle=Muscle.LATS),
            MovementWeaknessSignal(
                movement_id=1,
                session_id=1,
                computed_at=computed_at,
                stalled=True,
                growth_rate=0.01,
                lagging=False,
                is_weak=True,
            ),
            MovementWeaknessSignal(
                movement_id=2,
                session_id=1,
                computed_at=computed_at,
                stalled=False,
                growth_rate=0.11,
                lagging=False,
                is_weak=False,
            ),
            MovementWeaknessSignal(
                movement_id=3,
                session_id=1,
                computed_at=computed_at,
                stalled=False,
                growth_rate=0.08,
                lagging=False,
                is_weak=False,
            ),
        ])
        db.commit()

    resp = client.get("/weak-points")

    assert resp.status_code == 200
    data = resp.json()
    assert data["movements"] == [
        {
            "movement_id": 1,
            "name": "Back Squat [PB]",
            "stalled": True,
            "lagging": False,
            "growth_rate": 0.01,
        },
        {
            "movement_id": 2,
            "name": "Leg Extension",
            "stalled": False,
            "lagging": False,
            "growth_rate": 0.11,
        },
        {
            "movement_id": 3,
            "name": "Lat Pulldown",
            "stalled": False,
            "lagging": False,
            "growth_rate": 0.08,
        },
    ]

    groups = {group["muscle"]: group for group in data["muscle_groups"]}
    assert groups == {
        "LATS": {
            "muscle": "LATS",
            "weak_count": 0,
            "total_count": 1,
            "weak_movements": [],
        },
        "QUADS": {
            "muscle": "QUADS",
            "weak_count": 1,
            "total_count": 2,
            "weak_movements": [
                {
                    "movement_id": 1,
                    "name": "Back Squat [PB]",
                    "stalled": True,
                    "lagging": False,
                    "growth_rate": 0.01,
                },
            ],
        },
    }


def test_get_weak_points_uses_only_latest_signal_per_movement():
    client, engine = _client()
    first_at = datetime(2026, 7, 19, 12, 0, 0)
    second_at = first_at + timedelta(hours=1)

    with DbSession(engine) as db:
        _seed_session(db, 1)
        _seed_session(db, 2)
        db.add_all([
            Movement(id=1, name="Standing OHP [PB]", base_name="Standing OHP", primary_muscle=Muscle.FRONT_DELT),
            # Inserted out of computed_at order on purpose: pins the dedup
            # logic to the computed_at comparison rather than insertion/
            # iteration order (an insertion-order-last bug would fail this).
            MovementWeaknessSignal(
                movement_id=1,
                session_id=2,
                computed_at=second_at,
                stalled=False,
                growth_rate=0.25,
                lagging=False,
                is_weak=False,
            ),
            MovementWeaknessSignal(
                movement_id=1,
                session_id=1,
                computed_at=first_at,
                stalled=True,
                growth_rate=None,
                lagging=True,
                is_weak=True,
            ),
        ])
        db.commit()

    resp = client.get("/weak-points")

    assert resp.status_code == 200
    assert resp.json() == {
        "muscle_groups": [
            {
                "muscle": "FRONT_DELT",
                "weak_count": 0,
                "total_count": 1,
                "weak_movements": [],
            },
        ],
        "movements": [
            {
                "movement_id": 1,
                "name": "Standing OHP [PB]",
                "stalled": False,
                "lagging": False,
                "growth_rate": 0.25,
            },
        ],
    }


def test_get_weak_points_keeps_primary_muscle_none_in_flat_list_only():
    client, engine = _client()

    with DbSession(engine) as db:
        _seed_session(db, 1)
        db.add_all([
            Movement(id=1, name="Technique Drill", base_name="Technique Drill", primary_muscle=None),
            MovementWeaknessSignal(
                movement_id=1,
                session_id=1,
                computed_at=datetime(2026, 7, 19, 12, 0, 0),
                stalled=True,
                growth_rate=None,
                lagging=True,
                is_weak=True,
            ),
        ])
        db.commit()

    resp = client.get("/weak-points")

    assert resp.status_code == 200
    assert resp.json() == {
        "muscle_groups": [],
        "movements": [
            {
                "movement_id": 1,
                "name": "Technique Drill",
                "stalled": True,
                "lagging": True,
                "growth_rate": None,
            },
        ],
    }
