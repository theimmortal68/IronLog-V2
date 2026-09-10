# Spec 24: WithingsCredentials model + migration

## Objective
Add the singleton `WithingsCredentials` table that stores Withings OAuth2 tokens, per `docs/superpowers/specs/2026-07-18-withings-body-scan-integration-design.md` §Components 1 (read it first).

## File targets
- Modify: `ironlog/models/library.py` — add `WithingsCredentials`, placed after `DailyReadiness` (which follows `EngineState`'s singleton pattern already in this file — mirror `EngineState`'s `id: Optional[int] = Field(default=1, primary_key=True)` convention exactly).
- Modify: `ironlog/models/__init__.py` — add `WithingsCredentials` to the `.library` import list (mirrors how `DailyReadiness` was added there in spec 21).
- New: `deploy/migrations/031_withings_credentials.sql`.
- New tests: `tests/test_withings_credentials_model.py`.

## The fix
```python
class WithingsCredentials(SQLModel, table=True):
    """Singleton (id==1) holding the Withings OAuth2 token pair. access_token
    and refresh_token both rotate automatically as the server calls the
    Withings API, which is why this lives in the DB rather than .env (a
    file would need scripted rewrites on every rotation)."""
    id: Optional[int] = Field(default=1, primary_key=True)
    access_token: str
    refresh_token: str
    token_expires_at: datetime
    last_synced_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

Do NOT use the `date`/`_Date` alias workaround from `DailyReadiness` — none of this model's fields are named `date`, so the plain `datetime` import already in this file is fine as-is.

Migration (single-statement, additive, no carve-out needed):
```sql
CREATE TABLE IF NOT EXISTS withingscredentials (
    id INTEGER NOT NULL,
    access_token VARCHAR NOT NULL,
    refresh_token VARCHAR NOT NULL,
    token_expires_at DATETIME NOT NULL,
    last_synced_at DATETIME,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
```

## Edge cases
- No row exists until the one-time OAuth authorization flow (spec 26) creates it — every consumer of this table (sync logic, spec 27) must handle "no credentials row yet" as a real, expected state (not authorized yet), not an error.
- `last_synced_at` starts `None` — the sync logic (spec 27) must treat `None` as "never synced, use a longer lookback window," not crash on a null comparison.
- Do not touch `EngineState`, `DailyReadiness`, or any other existing model in this file — this is a pure addition.

## Dependencies
None — standalone, no shared files with spec 25 (parallel-safe).

## Verification
- New tests: round-trip of all fields including `last_synced_at=None`; confirm `id` defaults to 1 (singleton pattern) same as `EngineState`.
- Migration/model parity: `tests/test_migrations.py::test_chain_matches_create_all` green.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 563 passing).
