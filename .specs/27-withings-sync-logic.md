# Spec 27: Withings measurement sync logic

## Objective
Add the shared `sync_withings_measurements()` function that fetches new weight/body-fat measurements from Withings' Measure API and upserts them into `DailyReadiness`, per the design doc §Components 5. This is the core logic reused by the webhook handler (spec 29), the nightly timer script (spec 28), and the manual trigger endpoint (spec 29).

## File targets
- New: `ironlog/integrations/withings.py` (alongside spec 26's `withings_auth.py` in the same package).
- New tests: `tests/test_withings_sync.py`.

## The fix

```python
async def sync_withings_measurements(db: Session) -> dict:
    """Fetches new weight (type 1) and fat-ratio (type 6) measurements
    from Withings since WithingsCredentials.last_synced_at, upserts each
    date's DailyReadiness row, advances last_synced_at, returns a summary
    dict (e.g. {"days_updated": N, "measurements_fetched": N})."""
    creds = db.exec(select(WithingsCredentials).where(WithingsCredentials.id == 1)).one_or_none()
    if creds is None:
        raise RuntimeError("Withings not yet authorized — run /integrations/withings/authorize first")

    # Refresh access token if expired (reuse withings_auth.refresh_access_token,
    # spec 26 — import it, do not reimplement token refresh here).

    # Lookback window: last_synced_at if set, else a 48h default lookback
    # (tolerates any gap before the very first sync — document this constant).

    # Call Withings' getmeas endpoint at https://wbsapi.withings.net/measure
    # (action=getmeas, meastypes=1,6, category=1 for real measurements not
    # goals) since the lookback timestamp. This is the same API host as
    # spec 26's WITHINGS_TOKEN_URL (https://wbsapi.withings.net/v2/oauth2)
    # — confirmed by the user directly against the Withings developer
    # portal, not guessed.

    # For each returned measurement grouped by date:
    #   - type 1 (weight, Withings reports in kg with a unit/exponent
    #     multiplier — CONVERT to lb, this codebase's bodyweight fields
    #     are in lb everywhere else, e.g. EngineState.bodyweight,
    #     DailyReadiness.bodyweight)
    #   - type 6 (fat ratio, reported as a percentage already or needs
    #     the same unit/exponent decoding — verify against Withings'
    #     actual API docs, do not assume no conversion is needed)
    #   - upsert DailyReadiness for that date: bodyweight/body_fat_pct set,
    #     bodyweight_source="withings" — OVERWRITES any existing manual
    #     value for that date's bodyweight (Withings wins, per the design
    #     doc's conflict-resolution decision). Does NOT touch resting_hr,
    #     sleep_ok, subjective_ok on that row.

    # Advance creds.last_synced_at to now, commit.
    ...
```

Withings' `getmeas` response nests values as `{value, unit}` pairs where the real measurement is `value * 10**unit` — this is a real, easy-to-get-wrong unit-decoding step (Withings' own docs call this out explicitly). Get the exact decoding right and test it with a realistic sample response, not a pre-decoded fixture that hides the bug.

## Edge cases
- **No credentials row yet** (never authorized) — raise a clear, actionable error, not a null-pointer-style crash.
- **`last_synced_at` is `None`** (first-ever sync) — use the documented default lookback (e.g. 48h), not an unbounded "fetch everything" call.
- **A date already has a manual `DailyReadiness` row with `sleep_ok`/`subjective_ok` set** — the upsert must preserve those fields untouched while overwriting `bodyweight`/`body_fat_pct`/`bodyweight_source`. This is the same "partial upsert must not null out other fields" pattern spec 23's `POST /readiness` endpoint already established — mirror that exact upsert approach (get-or-create-then-selectively-update), do not reinvent it.
- **Withings returns zero new measurements** (nothing changed since last sync) — a clean no-op, `last_synced_at` still advances to "now" (confirms the sync ran, even if nothing new existed), summary reports 0 updates.
- **Withings API call fails** (network error, expired refresh token, revoked authorization) — raise/log clearly; do not silently swallow the error and advance `last_synced_at` as if it succeeded (that would create a silent data gap on the next sync's lookback window).

## Dependencies
Depends on spec 24 (`WithingsCredentials`) AND spec 25 (`DailyReadiness.body_fat_pct`) both merged first. Also imports `ironlog/integrations/withings_auth.py` (spec 26) for token refresh — if spec 26 hasn't merged yet when this is dispatched, coordinate merge order (see routing plan).

## Verification
- New tests, all against a **mocked** Withings API response (no real HTTP calls):
  - Correct unit/exponent decoding for both weight and fat-ratio values (use a realistic raw `{value, unit}` pair and assert the decoded lb/percent value).
  - Weight-in-kg-to-lb conversion is correct.
  - Upsert preserves an existing manual row's `sleep_ok`/`subjective_ok` while overwriting `bodyweight`/`body_fat_pct`.
  - `last_synced_at=None` uses the default lookback; `last_synced_at` set uses that exact watermark.
  - Zero-new-measurements case: `last_synced_at` still advances, summary reports 0.
  - No-credentials-row case: raises a clear error.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q`.
