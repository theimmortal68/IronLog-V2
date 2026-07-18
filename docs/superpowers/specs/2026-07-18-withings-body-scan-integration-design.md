# Withings Body-Scan Integration — Design

## Problem

The recovery/readiness check-in feature (specs 21-23, merged 2026-07-18) added `DailyReadiness.bodyweight`/`bodyweight_source` with `source` defaulting to `"manual"` — a deliberate seam for future wearable sync, not built out yet. The athlete has a Withings body-composition scale and wants bodyweight and body-fat % to sync automatically instead of typed in daily.

This is the first of three deferred wearable integrations (Withings, Polar Verity Sense, Samsung Watch) named in the original recovery/readiness design doc's "Explicitly out of scope" section, now being designed for real.

**Explicitly out of scope for this design** (see the companion decomposition decision below): weight/body-fat **goals** — i.e. making the CUT→STAB phase gate's currently-hardcoded `cut_to_stab_target: float = 213.0` (`ironlog/engine/analysis.py:55`) a real, user-settable value, and deciding what a body-fat-% goal drives. That touches a stated engine invariant (the phase gate) and gets its own separate brainstorming session and spec, not a bolt-on here.

## Scope decisions (from brainstorming)

- **Transport: webhook push, not polling.** `myflix.media` already has a real, publicly-reachable Traefik route (`plex.myflix.media` → `192.168.1.7:32400`, Let's Encrypt via Cloudflare DNS-01, confirmed genuinely internet-reachable, not just LAN-only cert theater). A new subdomain route makes real-time webhook receipt viable, so this design uses Withings' official push mechanism instead of pure polling.
- **Nightly reconciliation stays as a backup net.** Webhooks can be missed (server downtime, transient failures) — a nightly systemd timer re-runs the same sync logic as a catch-up, mirroring Flixd's existing `flixd-season-backfill.timer`/`.service` pattern on this same server.
- **Manual "sync now" trigger** for on-demand sync (just stepped off the scale, don't want to wait).
- **Data scope: bodyweight + body_fat_pct. NOT resting HR.** The athlete's scale reports HR via hand-contact sensors, but it's unreliable (spikes on first-thing-in-the-morning use) — resting HR stays manual-entry-only for now, with a future Polar Verity Sense / Samsung Watch integration as the real intended source.
- **Conflict resolution: Withings wins.** If a manual `POST /readiness` and a Withings sync both touch the same day's `bodyweight`, the Withings value (and its `bodyweight_source="withings"`) overwrites the manual entry — a scale reading is more precise than a typed-in number. `resting_hr`/`sleep_ok`/`subjective_ok` are untouched by Withings sync regardless (it never has that data).
- **`body_fat_pct` has no `_source` field.** Unlike `bodyweight`/`resting_hr`, only Withings will ever populate it — no other integration or manual-entry path writes it, so there's no provenance ambiguity to track.
- **`body_fat_pct` drives no gate logic in this design.** It's captured for a future trend view / manual reference ("track along with bodyweight trends to meet goals") — zero behavior change to `run_analysis.py`'s phase gate. Matches this codebase's established "capture now, decide later" pattern (e.g. `BandPair.calibration_status` MODELED before MEASURED).
- **API registration: Withings "Public API integration"** (confirmed via the athlete's live developer-portal screenshot, not the SDK or Cellular/Logistics options — those are for embedding in a mobile app or hardware-distribution programs, irrelevant here).
- **Token storage: DB, not `.env`.** A new `WithingsCredentials` singleton row (mirrors the existing `EngineState` singleton pattern) holds `access_token`/`refresh_token`, since the refresh token rotates automatically as the server calls the API — a `.env` file would need to be rewritten on every rotation, more fragile than a DB row for a value that changes on its own. `client_id`/`client_secret` (which don't rotate) go in `.env`, same as other secrets in this stack (`JELLYFIN_TOKEN` etc.).

## Components

### 1. `WithingsCredentials` model + migration

New singleton table (`ironlog/models/library.py`, next to `EngineState`):

```python
class WithingsCredentials(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    access_token: str
    refresh_token: str
    token_expires_at: datetime
    last_synced_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

`last_synced_at` is the watermark the sync logic uses to fetch "measurements since last sync" — both the webhook-triggered pull and the nightly reconciliation share this same watermark, so a webhook-driven sync advances it and the nightly job only re-fetches what's actually new.

Migration `031_withings_credentials.sql` — additive `CREATE TABLE IF NOT EXISTS`, single statement, no carve-out needed.

### 2. `DailyReadiness.body_fat_pct` + migration

One new nullable field on the existing model (spec 21, already live):

```python
body_fat_pct: Optional[float] = None
```

Migration `032_daily_readiness_body_fat.sql` — additive `ALTER TABLE ... ADD COLUMN`, single statement.

(Numbered 031/032 assuming this design's specs dispatch before any other pending migration claims those numbers first — confirm against `deploy/migrations/`'s actual latest file at spec-writing time.)

### 3. OAuth2 authorization flow

- `GET /integrations/withings/authorize` — redirects the browser to Withings' OAuth2 authorization URL (scope: `user.metrics`), constructed from `WITHINGS_CLIENT_ID`/`WITHINGS_CLIENT_SECRET` (`.env`) and a fixed redirect URI pointing at the new callback route.
- `GET /integrations/withings/callback` — receives the authorization code, exchanges it for `access_token`/`refresh_token` via Withings' token endpoint, upserts the singleton `WithingsCredentials` row. One-time manual step (visit `/authorize` in a browser once); all subsequent API calls use the stored refresh token to mint new access tokens server-side, no browser interaction needed again unless Withings revokes access.

### 4. Webhook receipt

- `POST /integrations/withings/webhook` — Withings' notification payload is deliberately thin: `{userid, appli}` (a measurement-type code), never the actual reading. The handler **never trusts the payload for data** — it triggers `sync_withings_measurements()` (component 5) to re-fetch via the authenticated Measure API. This mirrors Flixd's existing Sonarr/Radarr webhook pattern ("never trust the push, always re-pull").
- No shared-secret verification exists in Withings' webhook spec (unlike Flixd's `X-Flixd-Webhook-Secret` header). The endpoint's only defense is that a forged/replayed notification can only trigger a harmless extra authenticated API pull — it can never inject arbitrary data, since the actual write only ever comes from Withings' own API response, not the webhook body.
- Traefik: new router in `dynamic/routes.yml` (or a new file, following the existing `flixd-web-admin-api.yml`-style per-service file convention), e.g. `Host(\`withings.myflix.media\`)` → `http://192.168.1.7:8000` (IronLog-V2's existing port), routed only to `/integrations/withings/*` paths if Traefik path-matching is used, or the whole service if a dedicated subdomain per-service is simpler — decide at spec-writing time based on whether IronLog-V2 needs any other public exposure (it doesn't today).

### 5. Sync logic (shared)

`sync_withings_measurements()` (new module, e.g. `ironlog/integrations/withings.py`):
- Reads `WithingsCredentials.last_synced_at` as the watermark (or a longer lookback like 48h on first-ever sync/no watermark, to tolerate any missed webhook window).
- Refreshes the access token if expired (using `refresh_token`).
- Calls Withings' `getmeas` endpoint for measurement types 1 (weight) and 6 (fat ratio) since the watermark.
- For each date with new data: upserts that day's `DailyReadiness` row — `bodyweight`/`body_fat_pct` set, `bodyweight_source="withings"`, overwriting any existing manual value for that field on that day (Withings wins). Does not touch `resting_hr`/`sleep_ok`/`subjective_ok`.
- Advances `last_synced_at` to the sync completion time.
- Called from: the webhook handler (real-time), a new nightly systemd timer/oneshot script `scripts/sync_withings.py` (mirrors `flixd-season-backfill.service`'s `Type=oneshot`, `ReadWritePaths`, `Nice=10` structure), and `POST /integrations/withings/sync-now` (manual trigger, same underlying call).

## Data flow

```
[Withings scale scan]
        │
        ▼
[Withings cloud] ──POST notification──▶ [/integrations/withings/webhook]
        │                                         │
        │                                         ▼
        │                              sync_withings_measurements()
        │                                         │
        │◀─────── GET /measure (getmeas) ────────┘
        │  (authenticated Measure API pull,
        │   NOT the webhook payload itself)
        ▼
[actual weight/fat% data]
        │
        ▼
  upsert DailyReadiness
  (bodyweight_source="withings")

Nightly timer + manual /sync-now trigger the same
sync_withings_measurements() as a catch-up / on-demand path.
```

## Testing approach

- Pure unit tests for `sync_withings_measurements()`'s upsert logic (Withings-wins-over-manual, `body_fat_pct` write, `resting_hr` untouched) against a fake Measure-API response — no real HTTP calls in tests.
- OAuth2 token refresh logic unit-tested against a fake token endpoint response (expired-token triggers refresh; valid token skips it).
- Webhook handler test: a POST with a valid `{userid, appli}` body triggers a call to the sync function (mocked) — does not assert anything about payload trust beyond "the payload alone never writes data."
- No live-Withings-API integration test (no test credentials in CI) — the one live verification is a manual smoke test after deploy: trigger a real scan, confirm the webhook fires and the row updates, matching this session's established "verify live" pattern for server-first features.

## Explicitly out of scope

- Weight/body-fat **goals** and making `cut_to_stab_target` real/settable — separate design, separate spec, because it touches the CUT→STAB phase gate (a stated engine invariant) rather than just data ingestion.
- Resting HR from Withings — excluded as unreliable per the athlete's own observation; Polar Verity Sense / Samsung Watch remain the intended future source for that gate signal.
- Polar Verity Sense / Samsung Watch integration themselves — separate, not-yet-designed follow-ons.
- Any UI/trend visualization for the new `body_fat_pct` data — client-side follow-on, not specced here.
