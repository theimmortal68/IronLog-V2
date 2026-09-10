# Spec 29: Withings webhook receipt + manual sync-now endpoint + Traefik route

## Objective
Add the public webhook endpoint that receives Withings' push notifications (triggering a real-time `sync_withings_measurements()` call), a manual on-demand sync endpoint, and the Traefik route that makes the webhook publicly reachable, per the design doc §Components 4.

## File targets
- Modify: `ironlog/api/app.py` — add two endpoints (see below).
- New: `deploy/traefik/withings-webhook.yml` (this repo doesn't currently check in Traefik config — this is server-ops config, tracked in `~/server-ops` on myflix, NOT this repo; **write the file content here as documentation/reference only**, and note in the PR/completion report that it must be manually copied to `/home/jstout/server-ops/apps/traefik/dynamic/withings-webhook.yml` on myflix and `daemon-reload`'d — this spec does not and cannot deploy it, since `~/server-ops` is a separate repo/directory this dispatch has no access to).
- New tests: `tests/test_withings_webhook.py`.

## The fix

### `ironlog/api/app.py` — two new endpoints
```python
@app.post("/integrations/withings/webhook")
async def withings_webhook(userid: str = Form(...), appli: str = Form(...), background_tasks: BackgroundTasks = ...):
    """Withings POSTs application/x-www-form-urlencoded {userid, appli}
    on a measurement notification. Never trust this payload for data --
    it only tells us "something changed," so we schedule a real
    sync_withings_measurements() call (via BackgroundTasks, mirroring
    this file's existing background_tasks.add_task usage in
    submit_session) rather than trusting appli/userid values directly.
    Return 200 immediately regardless of what the background sync finds
    (Withings expects a fast ack, not a sync result)."""
    ...

@app.post("/integrations/withings/sync-now")
async def withings_sync_now(db: Session = Depends(...)):
    """Manual on-demand trigger -- calls sync_withings_measurements()
    synchronously and returns its summary dict directly (unlike the
    webhook, a manual trigger's caller wants to know what happened)."""
    ...
```

Withings' notification callback POSTs as `application/x-www-form-urlencoded`, not JSON — use FastAPI's `Form(...)` parameter style, not a Pydantic request body model (check the current Withings notification-callback documentation to confirm the exact field names/content-type before implementing; do not assume JSON).

### `deploy/traefik/withings-webhook.yml` (reference file — see note above on where it actually deploys)
Mirror the existing `flixd-web-admin-api.yml` shape in `~/server-ops/apps/traefik/dynamic/` (a path/PathPrefix-scoped route to one service):
```yaml
http:
  routers:
    ironlog-withings:
      rule: Host(`withings.myflix.media`)
      entryPoints:
        - websecure
      service: ironlog-withings
      tls:
        certResolver: cloudflare

  services:
    ironlog-withings:
      loadBalancer:
        servers:
          - url: http://192.168.1.7:8000
```
A dedicated subdomain (not a path prefix on an existing host) is used here since IronLog-V2 has no other public exposure today — simpler than introducing path-based routing for a single new route. Also needs a DNS record for `withings.myflix.media` pointed at the same Cloudflare zone as `plex.myflix.media`/`admin.myflix.media` — note this as a manual deploy-time step, not something this spec can do.

## Edge cases
- The webhook endpoint must return HTTP 200 quickly even if the background sync itself later fails — Withings retries/backs off based on the *webhook response*, not the sync outcome. Do not make the webhook response wait on the full sync.
- The webhook has no signature/shared-secret verification (Withings' notification API doesn't provide one) — this is a known, accepted gap per the design doc (a forged notification only triggers a harmless extra authenticated pull, never a direct data write). Do not add speculative verification logic that isn't part of Withings' actual API contract.
- `withings_sync_now` should surface a clear, actionable error (not a raw 500) if Withings isn't yet authorized (mirrors spec 27's not-yet-authorized error).

## Dependencies
Depends on spec 26 (OAuth flow, for the underlying credentials) AND spec 27 (`sync_withings_measurements`) both merged first.

## Verification
- New tests: webhook endpoint returns 200 immediately and schedules a background task (mock `sync_withings_measurements`, assert it was scheduled/called, not that the endpoint blocks on it); `sync-now` endpoint calls the sync function synchronously and returns its summary; `sync-now` surfaces a clear error when not-yet-authorized (mock the not-authorized `RuntimeError` from spec 27).
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q`.
- **Deploy-time only** (not part of this spec's merge gate): copy `deploy/traefik/withings-webhook.yml`'s content to the actual `~/server-ops` Traefik dynamic-config directory on myflix, add the DNS record, confirm Traefik picks up the new router (`docker logs traefik` or the dashboard), then do a real end-to-end smoke test (trigger a real Withings notification or use their test-webhook tool if one exists) before considering this feature fully live.
