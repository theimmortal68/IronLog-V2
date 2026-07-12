"""live_seed_warmup.py — one-off idempotent live-DB warmup seed.

Backfills the static per-day warmup_config JSON added by migration 027 onto the
existing Phase 1 ProgramDay rows. Mirrors live_seed_ramp_and_finishers.py's
production-safe pattern: update rows only when warmup_config is NULL, leave
already-populated rows untouched, and never reseed the program structure.

NO from __future__ import annotations.
"""
from sqlmodel import Session, select

from ironlog.generation.program_seed import WARMUP_CONFIGS
from ironlog.models.program import ProgramDay


def apply(db: Session) -> None:
    days_by_index = {
        pd.day_index: pd
        for pd in db.exec(select(ProgramDay)).all()
    }
    missing = sorted(day_index for day_index in WARMUP_CONFIGS if day_index not in days_by_index)
    if missing:
        raise ValueError(f"HALT-AND-FLAG: no ProgramDay row(s) for day_index={missing!r} in live DB.")

    changed = 0
    already_set = 0
    for day_index, config in WARMUP_CONFIGS.items():
        program_day = days_by_index[day_index]
        if program_day.warmup_config is not None:
            already_set += 1
            continue
        program_day.warmup_config = dict(config)
        db.add(program_day)
        changed += 1

    db.commit()
    print(f"warmups: {changed} ProgramDay row(s) updated, {already_set} already set.")


def main() -> None:
    from ironlog.db import engine
    with Session(engine) as db:
        apply(db)


if __name__ == "__main__":
    main()
