# Spec 41: Nightly missed-days detection timer

## Objective
Add a systemd timer + oneshot service that runs `check_missed_days()` nightly, per the design doc §Components 1.

## File targets
- New: `scripts/check_missed_days.py` — standalone oneshot entrypoint (mirrors `scripts/sync_withings.py`'s exact shape).
- New: `deploy/ironlog-missed-days.timer`.
- New: `deploy/ironlog-missed-days.service`.

## The fix

`scripts/check_missed_days.py` (mirror `scripts/sync_withings.py` exactly, no `asyncio` needed here since `check_missed_days` is synchronous, not async):
```python
"""Oneshot missed-training-day detection -- run nightly via systemd timer,
also runnable manually for debugging."""
from sqlmodel import Session

from ironlog.db import engine
from ironlog.persistence.missed_days import check_missed_days


def main():
    with Session(engine) as db:
        result = check_missed_days(db)
        print(f"Missed-days check: {result}")


if __name__ == "__main__":
    main()
```

`deploy/ironlog-missed-days.timer` (mirror `deploy/ironlog-withings-sync.timer`'s exact shape, different time to avoid clustering — e.g. 03:30 instead of Withings' 03:00):
```ini
[Unit]
Description=Nightly missed-training-day detection

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/ironlog-missed-days.service` (mirror `deploy/ironlog-withings-sync.service`'s exact shape — confirmed current values below, copied from that file, use these exact values, do not guess):
```ini
[Unit]
Description=Missed-training-day detection
After=ironlogv2.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=jstout
Group=jstout
WorkingDirectory=/home/jstout/projects/IronLog-V2
EnvironmentFile=-/home/jstout/projects/IronLog-V2/.env
ExecStart=/home/jstout/projects/IronLog-V2/.venv/bin/python scripts/check_missed_days.py
TimeoutStartSec=300
Nice=10
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/jstout/projects/IronLog-V2
```
This service does NOT need `network-online.target` for its own function (it's pure local DB work, no external API calls, unlike the Withings sync) — but `After=ironlogv2.service` is still worth keeping so it doesn't race the main service's own migration step on a fresh boot. Keep `Wants=network-online.target` for consistency with the sibling timer's exact shape even though this particular script doesn't need network — do not diverge unless you have a concrete reason to.

## Edge cases
- These are deploy-time infra files, not something automated tests exercise — do NOT write a pytest test that tries to parse/validate the `.timer`/`.service` INI syntax (no test exists for the sibling `deploy/ironlog-withings-sync.*` files either).
- `scripts/check_missed_days.py` should be import-safe (importing it shouldn't execute `main()` — the `if __name__ == "__main__"` guard handles this).
- If `check_missed_days` raises (shouldn't under normal operation per spec 40's edge cases, which all resolve to a zero-count summary rather than raising), let the exception propagate uncaught so the process exits non-zero and systemd/journald records the failure clearly — same convention as `scripts/sync_withings.py`.

## Dependencies
Depends on spec 40 (`check_missed_days`) merged first.

## Verification
- `scripts/check_missed_days.py` imports cleanly: `~/projects/IronLog-V2/.venv/bin/python -c "import scripts.check_missed_days"`.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 624 passing, or higher if spec 40 already merged — this spec adds no new tests, just confirm no regressions).
- **Deploy-time only** (not part of this spec's merge gate): `systemctl daemon-reload && systemctl enable --now ironlog-missed-days.timer` on myflix (following the exact install procedure already used for `ironlog-withings-sync.timer` — the repo's `deploy/` files are not auto-installed, this requires a one-time manual copy into `/etc/systemd/system/`), confirm via `systemctl list-timers`, and one manual test run.
