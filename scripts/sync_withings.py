"""Oneshot Withings reconciliation sync -- run nightly via systemd timer,
also runnable manually for debugging."""
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
