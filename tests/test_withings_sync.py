import asyncio
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.models import WithingsCredentials, DailyReadiness
from ironlog.integrations.withings import (
    DEFAULT_LOOKBACK_HOURS,
    WithingsAPIError,
    WithingsNotAuthorizedError,
    httpx,
    sync_withings_measurements,
)


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="db")
def db_fixture(engine):
    with Session(engine) as session:
        yield session


def _mock_measure_response(monkeypatch, payload, status_code=200):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, data, headers):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return FakeResponse(payload, status_code)

    class FakeResponse:
        def __init__(self, response_payload, code):
            self._payload = response_payload
            self.status_code = code

        def json(self):
            return self._payload

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return captured


def test_no_credentials_raises_error(db):
    with pytest.raises(WithingsNotAuthorizedError, match="Withings not yet authorized"):
        asyncio.run(sync_withings_measurements(db))


def test_sync_fetches_and_upserts(db, monkeypatch):
    now = datetime.utcnow()
    last_synced_at = now - timedelta(days=1)
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now + timedelta(hours=1),
        last_synced_at=last_synced_at,
        updated_at=now,
    )
    db.add(creds)
    
    # Pre-existing manual readiness for the date we'll mock
    target_date = datetime.fromtimestamp(1690000000).date()
    db.add(DailyReadiness(
        date=target_date,
        bodyweight=150.0,
        bodyweight_source="manual",
        sleep_ok=True,
        subjective_ok=False,
    ))
    db.commit()
    
    captured = _mock_measure_response(
        monkeypatch,
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "date": 1690000000,
                        "measures": [
                            {"value": 805, "unit": -1, "type": 1},  # 80.5 kg -> 177.47191 lb
                            {"value": 185, "unit": -1, "type": 6},  # 18.5%
                        ]
                    },
                    {
                        "date": 1690086400,
                        "measures": [
                            {"value": 100, "unit": 0, "type": 5}  # Ignored type
                        ]
                    }
                ]
            }
        }
    )
    
    res = asyncio.run(sync_withings_measurements(db))
    assert res == {"days_updated": 1, "measurements_fetched": 3}
    
    assert captured["data"]["startdate"] == int(last_synced_at.timestamp())
    assert captured["headers"]["Authorization"] == "Bearer acc"
    
    readiness = db.exec(select(DailyReadiness).where(DailyReadiness.date == target_date)).first()
    assert readiness is not None
    assert abs(readiness.bodyweight - (80.5 * 2.20462)) < 0.001
    assert readiness.body_fat_pct == 18.5
    assert readiness.bodyweight_source == "withings"
    assert readiness.sleep_ok is True
    assert readiness.subjective_ok is False

    ignored_date = datetime.fromtimestamp(1690086400).date()
    assert db.exec(select(DailyReadiness).where(DailyReadiness.date == ignored_date)).first() is None

    updated_creds = db.exec(select(WithingsCredentials)).first()
    assert updated_creds.last_synced_at > last_synced_at


def test_sync_no_new_measurements(db, monkeypatch):
    now = datetime.utcnow()
    last_synced_at = now - timedelta(days=1)
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now + timedelta(hours=1),
        last_synced_at=last_synced_at,
        updated_at=now,
    )
    db.add(creds)
    db.commit()
    
    _mock_measure_response(
        monkeypatch,
        {
            "status": 0,
            "body": {
                "measuregrps": []
            }
        }
    )
    
    res = asyncio.run(sync_withings_measurements(db))
    assert res == {"days_updated": 0, "measurements_fetched": 0}
    
    updated_creds = db.exec(select(WithingsCredentials)).first()
    assert updated_creds.last_synced_at > last_synced_at


def test_sync_first_time_uses_default_lookback(db, monkeypatch):
    now = datetime.utcnow()
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now + timedelta(hours=1),
        last_synced_at=None,
        updated_at=now,
    )
    db.add(creds)
    db.commit()
    
    captured = _mock_measure_response(monkeypatch, {"status": 0, "body": {"measuregrps": []}})
    
    asyncio.run(sync_withings_measurements(db))
    
    expected_start = int((now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)).timestamp())
    # Allow 1 second delta
    assert abs(captured["data"]["startdate"] - expected_start) <= 1


def test_sync_api_failure_does_not_advance_last_synced_at(db, monkeypatch):
    now = datetime.utcnow()
    last_synced_at = now - timedelta(days=1)
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now + timedelta(hours=1),
        last_synced_at=last_synced_at,
        updated_at=now,
    )
    db.add(creds)
    db.commit()
    
    _mock_measure_response(
        monkeypatch,
        {"status": 293, "error": "Invalid auth token"}
    )
    
    with pytest.raises(WithingsAPIError, match="status 293"):
        asyncio.run(sync_withings_measurements(db))
        
    updated_creds = db.exec(select(WithingsCredentials)).first()
    assert updated_creds.last_synced_at == last_synced_at


def test_sync_http_failure_does_not_advance_last_synced_at(db, monkeypatch):
    now = datetime.utcnow()
    last_synced_at = now - timedelta(days=1)
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now + timedelta(hours=1),
        last_synced_at=last_synced_at,
        updated_at=now,
    )
    db.add(creds)
    db.commit()
    
    _mock_measure_response(monkeypatch, {}, status_code=500)
    
    with pytest.raises(WithingsAPIError, match="HTTP 500"):
        asyncio.run(sync_withings_measurements(db))
        
    updated_creds = db.exec(select(WithingsCredentials)).first()
    assert updated_creds.last_synced_at == last_synced_at


def test_token_refresh_integration(db, monkeypatch):
    now = datetime.utcnow()
    last_synced_at = now - timedelta(days=1)
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now - timedelta(hours=1), # Expired!
        last_synced_at=last_synced_at,
        updated_at=now,
    )
    db.add(creds)
    db.commit()

    monkeypatch.setenv("WITHINGS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("WITHINGS_CLIENT_SECRET", "mock_secret")

    # Mock the actual httpx client because it handles both refresh and measure in one go
    # The first call will be to TOKEN_URL, second to MEASURE_URL
    class FakeAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
        async def post(self, url, data, **kwargs):
            if "oauth2" in url:
                return FakeResponse({"status": 0, "body": {"access_token": "new_acc", "refresh_token": "new_ref", "expires_in": 3600}}, 200)
            elif "measure" in url:
                self.measure_headers = kwargs.get("headers")
                return FakeResponse({"status": 0, "body": {"measuregrps": []}}, 200)

    class FakeResponse:
        def __init__(self, data, code):
            self.data = data
            self.status_code = code
        def json(self):
            return self.data

    fake_client = FakeAsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake_client)
    
    asyncio.run(sync_withings_measurements(db))
    
    assert fake_client.measure_headers["Authorization"] == "Bearer new_acc"
    
    updated_creds = db.exec(select(WithingsCredentials)).first()
    assert updated_creds.access_token == "new_acc"
    assert updated_creds.refresh_token == "new_ref"


def test_token_refresh_persists_even_if_measure_fails(db, monkeypatch):
    now = datetime.utcnow()
    last_synced_at = now - timedelta(days=1)
    
    creds = WithingsCredentials(
        id=1,
        access_token="acc",
        refresh_token="ref",
        token_expires_at=now - timedelta(hours=1), # Expired!
        last_synced_at=last_synced_at,
        updated_at=now,
    )
    db.add(creds)
    db.commit()

    monkeypatch.setenv("WITHINGS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("WITHINGS_CLIENT_SECRET", "mock_secret")

    class FakeAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
        async def post(self, url, data, **kwargs):
            if "oauth2" in url:
                return FakeResponse({"status": 0, "body": {"access_token": "new_acc", "refresh_token": "new_ref", "expires_in": 3600}}, 200)
            elif "measure" in url:
                return FakeResponse({"status": 293, "error": "Some failure"}, 200)

    class FakeResponse:
        def __init__(self, data, code):
            self.data = data
            self.status_code = code
        def json(self):
            return self.data

    fake_client = FakeAsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake_client)
    
    with pytest.raises(WithingsAPIError, match="status 293"):
        asyncio.run(sync_withings_measurements(db))
        
    db.rollback()
    
    updated_creds = db.exec(select(WithingsCredentials)).first()
    assert updated_creds.access_token == "new_acc"
    assert updated_creds.refresh_token == "new_ref"
