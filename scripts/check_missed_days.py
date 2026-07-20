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
