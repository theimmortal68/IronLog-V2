import asyncio
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.api.app as api_app
import ironlog.models  # noqa: F401
from ironlog.integrations import withings_auth
from ironlog.models import WithingsCredentials


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    withings_auth._pending_oauth_state = None
    yield
    api_app.app.dependency_overrides.clear()
    withings_auth._pending_oauth_state = None


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


def _mock_token_response(monkeypatch, payload):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, data):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse(payload)

    class FakeResponse:
        status_code = 200

        def __init__(self, response_payload):
            self._payload = response_payload

        def json(self):
            return self._payload

    monkeypatch.setattr(withings_auth.httpx, "AsyncClient", FakeAsyncClient)
    return captured


def _set_withings_env(monkeypatch):
    monkeypatch.setenv("WITHINGS_CLIENT_ID", "client-id")
    monkeypatch.setenv("WITHINGS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("WITHINGS_REDIRECT_URI", "http://testserver/integrations/withings/callback")


def test_build_authorize_url_contains_expected_query_params():
    url = withings_auth.build_authorize_url(
        "client-id",
        "http://localhost/callback",
        "state-token",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert url.startswith(withings_auth.WITHINGS_AUTHORIZE_URL)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["http://localhost/callback"]
    assert params["state"] == ["state-token"]
    assert params["scope"] == ["user.metrics"]


def test_pending_oauth_state_round_trip_consumes_once():
    withings_auth.set_pending_state("state-token")

    assert withings_auth.consume_pending_state("state-token") is True
    assert withings_auth.consume_pending_state("state-token") is False


def test_pending_oauth_state_mismatch_does_not_clear():
    withings_auth.set_pending_state("expected-state")

    assert withings_auth.consume_pending_state("wrong-state") is False
    assert withings_auth.consume_pending_state("expected-state") is True


def test_exchange_code_for_tokens_parses_success_body(monkeypatch):
    captured = _mock_token_response(
        monkeypatch,
        {
            "status": 0,
            "body": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 10800,
                "token_type": "Bearer",
            },
        },
    )

    tokens = asyncio.run(
        withings_auth.exchange_code_for_tokens(
            "client-id",
            "client-secret",
            "auth-code",
            "http://localhost/callback",
        )
    )

    assert tokens == {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 10800,
    }
    assert captured["url"] == withings_auth.WITHINGS_TOKEN_URL
    assert captured["data"] == {
        "action": "requesttoken",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "code": "auth-code",
        "redirect_uri": "http://localhost/callback",
        "grant_type": "authorization_code",
    }


def test_exchange_code_for_tokens_raises_on_nonzero_withings_status(monkeypatch):
    _mock_token_response(
        monkeypatch,
        {"status": 293, "body": {"error": "invalid authorization code"}},
    )

    with pytest.raises(RuntimeError, match="293.*invalid authorization code"):
        asyncio.run(
            withings_auth.exchange_code_for_tokens(
                "client-id",
                "client-secret",
                "bad-code",
                "http://localhost/callback",
            )
        )


def test_refresh_access_token_parses_success_body(monkeypatch):
    captured = _mock_token_response(
        monkeypatch,
        {
            "status": 0,
            "body": {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 10800,
            },
        },
    )

    tokens = asyncio.run(
        withings_auth.refresh_access_token(
            "client-id",
            "client-secret",
            "refresh-token",
        )
    )

    assert tokens == {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 10800,
    }
    assert captured["url"] == withings_auth.WITHINGS_TOKEN_URL
    assert captured["data"] == {
        "action": "requesttoken",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
        "grant_type": "refresh_token",
    }


def test_refresh_access_token_raises_on_nonzero_withings_status(monkeypatch):
    _mock_token_response(
        monkeypatch,
        {"status": 401, "body": {"error_description": "refresh token expired"}},
    )

    with pytest.raises(RuntimeError, match="401.*refresh token expired"):
        asyncio.run(
            withings_auth.refresh_access_token(
                "client-id",
                "client-secret",
                "expired-refresh-token",
            )
        )


def test_withings_authorize_fails_loudly_without_client_id(monkeypatch):
    client, _ = _client()
    monkeypatch.delenv("WITHINGS_CLIENT_ID", raising=False)
    monkeypatch.setenv("WITHINGS_REDIRECT_URI", "http://localhost/callback")

    response = client.get("/integrations/withings/authorize")

    assert response.status_code == 500
    assert response.json()["detail"] == "WITHINGS_CLIENT_ID is not configured"


def test_withings_callback_inserts_credentials(monkeypatch):
    client, engine = _client()
    _set_withings_env(monkeypatch)
    state = "oauth-state"
    withings_auth.set_pending_state(state)
    captured = {}

    async def fake_exchange(client_id, client_secret, code, redirect_uri):
        captured["args"] = (client_id, client_secret, code, redirect_uri)
        return {
            "access_token": "first-access",
            "refresh_token": "first-refresh",
            "expires_in": 3600,
        }

    monkeypatch.setattr(api_app, "exchange_code_for_tokens", fake_exchange)
    before = datetime.utcnow()

    response = client.get(
        "/integrations/withings/callback",
        params={"code": "auth-code", "state": state},
    )

    after = datetime.utcnow()
    assert response.status_code == 200
    assert response.json() == {"status": "authorized"}
    assert withings_auth.consume_pending_state(state) is False
    assert captured["args"] == (
        "client-id",
        "client-secret",
        "auth-code",
        "http://testserver/integrations/withings/callback",
    )
    with DbSession(engine) as db:
        credentials = db.get(WithingsCredentials, 1)
        assert credentials is not None
        assert credentials.access_token == "first-access"
        assert credentials.refresh_token == "first-refresh"
        assert before + timedelta(seconds=3600) <= credentials.token_expires_at
        assert credentials.token_expires_at <= after + timedelta(seconds=3600)


def test_withings_callback_updates_existing_credentials(monkeypatch):
    client, engine = _client()
    _set_withings_env(monkeypatch)
    state = "update-state"
    last_synced_at = datetime(2026, 7, 18, 10, 0, 0)
    with DbSession(engine) as db:
        db.add(
            WithingsCredentials(
                id=1,
                access_token="old-access",
                refresh_token="old-refresh",
                token_expires_at=datetime(2026, 7, 18, 11, 0, 0),
                last_synced_at=last_synced_at,
                updated_at=datetime(2026, 7, 18, 10, 30, 0),
            )
        )
        db.commit()

    async def fake_exchange(client_id, client_secret, code, redirect_uri):
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 7200,
        }

    monkeypatch.setattr(api_app, "exchange_code_for_tokens", fake_exchange)
    withings_auth.set_pending_state(state)

    response = client.get(
        "/integrations/withings/callback",
        params={"code": "new-code", "state": state},
    )

    assert response.status_code == 200
    with DbSession(engine) as db:
        rows = db.exec(select(WithingsCredentials)).all()
        assert len(rows) == 1
        credentials = rows[0]
        assert credentials.id == 1
        assert credentials.access_token == "new-access"
        assert credentials.refresh_token == "new-refresh"
        assert credentials.last_synced_at == last_synced_at
        assert credentials.token_expires_at > datetime.utcnow()


def test_withings_callback_rejects_missing_state_before_token_exchange(monkeypatch):
    client, _ = _client()
    _set_withings_env(monkeypatch)
    exchange_called = False

    async def fake_exchange(client_id, client_secret, code, redirect_uri):
        nonlocal exchange_called
        exchange_called = True
        return {}

    monkeypatch.setattr(api_app, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/integrations/withings/callback",
        params={"code": "auth-code"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired OAuth state"
    assert exchange_called is False


def test_withings_callback_rejects_wrong_state_without_consuming_expected(monkeypatch):
    client, _ = _client()
    _set_withings_env(monkeypatch)
    withings_auth.set_pending_state("expected-state")
    exchange_called = False

    async def fake_exchange(client_id, client_secret, code, redirect_uri):
        nonlocal exchange_called
        exchange_called = True
        return {}

    monkeypatch.setattr(api_app, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/integrations/withings/callback",
        params={"code": "auth-code", "state": "wrong-state"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired OAuth state"
    assert exchange_called is False
    assert withings_auth.consume_pending_state("expected-state") is True
