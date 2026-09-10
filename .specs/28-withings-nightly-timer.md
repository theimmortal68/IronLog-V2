# Spec 28: Nightly Withings sync timer (reconciliation backup)

## Objective
Add a systemd timer + oneshot service that runs `sync_withings_measurements()` (spec 27) nightly, as a catch-up net for any webhook notification the server missed (downtime, transient failure), per the design doc §Components 5 and §Scope decisions ("nightly reconciliation stays as a backup net").

## File targets
- New: `scripts/sync_withings.py` — standalone oneshot entrypoint.
- New: `deploy/ironlog-withings-sync.timer`.
- New: `deploy/ironlog-withings-sync.service`.

## The fix

`scripts/sync_withings.py`:
```python
"""Oneshot Withings reconciliation sync — run nightly via systemd timer,
also runnable manually for debugging. Mirrors the standalone-script
pattern of Flixd's scripts/backfill_season_counts.py."""
import asyncio
from sqlmodel import Session
from ironlog.db import engine
from ironlog.integrations.withings import sync_withings_measurements

def main():
    with Session(engine) as db:
        result = asyncio.run(sync_withings_measurements(db))
        print(f"Withings sync: {result}")

if __name__ == "__main__":
    main()
```

Check `ironlog/db.py` for the actual `engine`/`Session` import path and mirror whatever pattern an existing standalone script in this repo already uses (if one exists) rather than inventing a new DB-connection bootstrap.

`deploy/ironlog-withings-sync.timer` (mirror `flixd-season-backfill.timer`'s exact shape, different schedule to avoid clustering with other nightly jobs on this server — e.g. 03:00 instead of Flixd's 04:00):
```ini
[Unit]
Description=Periodic Withings body-scan reconciliation sync (catches missed webhooks)

[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/ironlog-withings-sync.service` (mirror `flixd-season-backfill.service`'s exact shape — `Type=oneshot`, hardening directives, working directory/venv path matching `deploy/ironlogv2.service`'s existing `WorkingDirectory`/`EnvironmentFile` values, not invented paths):
```ini
[Unit]
Description=Withings body-scan reconciliation sync
After=ironlogv2.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=jstout
Group=jstout
WorkingDirectory=/home/jstout/projects/IronLog-V2
EnvironmentFile=-/home/jstout/projects/IronLog-V2/.env
ExecStart=/home/jstout/projects/IronLog-V2/.venv/bin/python scripts/sync_withings.py
TimeoutStartSec=300
Nice=10
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/jstout/projects/IronLog-V2
```

Copy the exact `WorkingDirectory`/venv path/`EnvironmentFile` values from the existing `deploy/ironlogv2.service` in this repo rather than the values shown above verbatim, in case they've drifted (e.g. confirm the actual path is `/home/jstout/projects/IronLog-V2` and not `/mnt/appdata/projects/IronLog-V2` — check both `deploy/ironlogv2.service` in this repo AND the live unit file on myflix if they might differ).

## Edge cases
- If `sync_withings_measurements` raises (not-yet-authorized, API failure), the script must exit non-zero so systemd/journald records the failure clearly (do not swallow the exception into a silent success).
- These are **deploy-time infra files, not something automated tests exercise** — verification here is a manual production check (see below), not a pytest case. Do not invent a pytest test that tries to parse/validate the `.timer`/`.service` INI syntax; that's not this codebase's testing convention (compare: no test exists for `deploy/ironlogv2.service` either).

## Dependencies
Depends on spec 27 (`sync_withings_measurements` must exist to call).

## Verification
- `scripts/sync_withings.py` runs manually against a real (or authorized-but-empty) dev DB without crashing on import (a smoke run, not a full pytest suite item).
- **Deploy-time only** (not part of this spec's merge gate, done when this feature is actually deployed): `systemctl daemon-reload && systemctl enable --now ironlog-withings-sync.timer` on myflix, confirm via `systemctl list-timers` it's scheduled, and `systemctl start ironlog-withings-sync.service` once manually to confirm a real run succeeds (or fails clearly if not-yet-authorized, which is expected pre-spec-26-deploy).
- Full server suite green (no new tests expected to be added by this spec, this is infra not application logic): `~/projects/IronLog-V2/.venv/bin/pytest -q`.
