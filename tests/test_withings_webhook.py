import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session as DbSession, create_engine
from sqlmodel.pool import StaticPool

import ironlog.api.app as api_app
import ironlog.models  # noqa: F401


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    api_app.app.dependency_overrides.clear()


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with DbSession(engine) as session:
            yield session

    api_app.app.dependency_overrides[api_app.get_session] = _override
    return TestClient(api_app.app), engine


def test_withings_webhook_accepts_form_and_schedules_sync(monkeypatch):
    scheduled = []

    def fake_add_task(self, func, *args, **kwargs):
        scheduled.append((func, args, kwargs))

    monkeypatch.setattr(api_app.BackgroundTasks, "add_task", fake_add_task)
    client, _ = _client()

    response = client.post(
        "/integrations/withings/webhook",
        data={"userid": "withings-user", "appli": "1"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert scheduled == [(api_app._sync_withings_measurements_background, (), {})]


def test_withings_sync_now_returns_sync_summary(monkeypatch):
    summary = {"days_updated": 2, "measurements_fetched": 5}
    calls = []

    async def fake_sync(db):
        calls.append(db)
        return summary

    monkeypatch.setattr(api_app, "sync_withings_measurements", fake_sync)
    client, _ = _client()

    response = client.post("/integrations/withings/sync-now")

    assert response.status_code == 200
    assert response.json() == summary
    assert len(calls) == 1


def test_withings_sync_now_maps_not_authorized_runtime_error(monkeypatch):
    async def fake_sync(db):
        # Exact production message from sync_withings_measurements (not a
        # fabricated stand-in) -- the 400/502 split is a substring match on
        # this wording, so this test must track the real string or a wording
        # change could silently flip the status code with no test failure.
        raise RuntimeError("Withings not yet authorized — run /integrations/withings/authorize first")

    monkeypatch.setattr(api_app, "sync_withings_measurements", fake_sync)
    client, _ = _client()

    response = client.post("/integrations/withings/sync-now")

    assert response.status_code == 400
    assert response.json()["detail"] == "Withings not yet authorized — run /integrations/withings/authorize first"


def test_withings_sync_now_maps_api_runtime_error(monkeypatch):
    async def fake_sync(db):
        raise RuntimeError("Withings measure request failed with HTTP 500")

    monkeypatch.setattr(api_app, "sync_withings_measurements", fake_sync)
    client, _ = _client()

    response = client.post("/integrations/withings/sync-now")

    assert response.status_code == 502
    assert response.json()["detail"] == "Withings measure request failed with HTTP 500"
