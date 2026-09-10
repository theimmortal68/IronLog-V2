# Spec 26: Withings OAuth2 authorization flow

## Objective
Add the one-time OAuth2 authorization endpoints that obtain and store Withings API tokens into `WithingsCredentials` (spec 24, must be merged first), per the design doc §Components 3.

## File targets
- New: `ironlog/integrations/__init__.py` (empty package init — this is the first file in a new `ironlog/integrations/` package; use this exact package name so spec 27's sync module lives alongside it as `ironlog/integrations/withings.py`, not a different location).
- New: `ironlog/integrations/withings_auth.py` — token exchange/refresh helper functions (used by both this spec's endpoints and spec 27's sync logic).
- Modify: `ironlog/api/app.py` — add two endpoints (see below). Follow this file's existing pattern: plain `@app.get`/`@app.post` decorators, a `Session = Depends(...)`-style DB session dependency if that's the established pattern here (check how `run_analysis`'s callers or `/bands/usable` obtain a `Session` — mirror it exactly, do not invent a new DB-session-acquisition pattern).
- New tests: `tests/test_withings_auth.py`.

## The fix

### `ironlog/integrations/withings_auth.py`
```python
import httpx

WITHINGS_AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
WITHINGS_TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Constructs the Withings OAuth2 authorization URL, scope=user.metrics."""
    ...

async def exchange_code_for_tokens(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """POSTs action=requesttoken to WITHINGS_TOKEN_URL. Returns the parsed
    {access_token, refresh_token, expires_in} on success. Raises on a
    non-zero Withings 'status' field in the response body (Withings API
    responses are HTTP 200 even on failure -- the real status is inside
    the JSON body, check it explicitly, do not assume HTTP 200 == success)."""
    ...

async def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """POSTs action=requesttoken with grant_type=refresh_token. Same
    status-field-in-body caveat as exchange_code_for_tokens."""
    ...
```

Read the current Withings API OAuth2 documentation (developer.withings.com) for the exact request/response field names and the `action=requesttoken` grant_type values — do not guess field names from a different OAuth2 provider's conventions. Withings' token endpoint wraps responses in `{"status": 0, "body": {...}}`; a non-zero `status` is an error even though the HTTP status code is 200.

### `ironlog/api/app.py` — two new endpoints
```python
@app.get("/integrations/withings/authorize")
def withings_authorize():
    """Redirects the browser to Withings' OAuth2 consent screen."""
    ...

@app.get("/integrations/withings/callback")
async def withings_callback(code: str, ...):
    """Exchanges the auth code for tokens, upserts the singleton
    WithingsCredentials row (id=1), returns a simple human-readable
    confirmation page/JSON (this is a one-time manual browser step,
    not a machine-consumed API response)."""
    ...
```

`WITHINGS_CLIENT_ID`/`WITHINGS_CLIENT_SECRET`/`WITHINGS_REDIRECT_URI` come from `os.environ.get(...)` — mirror the exact pattern already used for `GEMINI_API_KEY` in this same file (`ironlog/api/app.py:169`), not a new settings/config class.

## Edge cases
- `withings_authorize` must fail loudly (e.g. `HTTPException(500, "WITHINGS_CLIENT_ID not configured")`) if the env vars aren't set — do not silently redirect to a broken URL.
- `withings_callback` upserts (not always-inserts) the singleton row — if `WithingsCredentials(id=1)` already exists (re-authorization), update it in place rather than erroring on a duplicate primary key.
- Withings' token endpoint returning a non-zero `status` in its JSON body must raise/surface a real error to the browser, not silently store garbage tokens.
- Do not implement the sync logic itself here (that's spec 27) — this spec is authorization/token-storage only.

## Dependencies
Depends on spec 24 (`WithingsCredentials` model must exist to store tokens into).

## Verification
- New tests: `build_authorize_url` produces a URL with the expected query params; `exchange_code_for_tokens`/`refresh_access_token` correctly parse a mocked httpx response (both a success body and a non-zero-`status` failure body — assert the failure case raises); `withings_callback` upserts `WithingsCredentials(id=1)` correctly on both first-auth and re-auth (existing row) cases, using a mocked token-exchange call (no real HTTP in tests).
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q`.
