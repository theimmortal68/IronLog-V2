"""reverse_nordic_ladder_fix.py — one-off idempotent live-DB fix.

Movement.assist_ladder was never populated for "Reverse Nordic Curl [GHR]"
(a gap in ironlog/seed.py's master movement definitions — no assist_ladder
key was ever included for any ASSISTED movement). This left the movement's
progression_rule (ASSISTANCE_REDUCTION, already correctly set) unable to
advance: advance.py's _assistance_reduction reads `movement.assist_ladder
or []`, so an empty ladder means the assist level can never step down
(lower assist = harder is the correct semantics; the movement just could
never progress toward it).

Scope is intentionally narrow: only this one movement's assist_ladder is
touched. See build-plan/reports for the broader assist_ladder-seeding gap
(Nordic Curl, Nordic Curl - Volume, Pull-up are also affected) — that is a
separate spec, not folded into this narrow live-data fix.

Idempotent: safe to re-run (sets assist_ladder unconditionally to the
target value; a second run makes no changes and isn't counted).
NO from __future__ import annotations.
"""
from sqlmodel import Session, select

from ironlog.models.library import Movement

MOVEMENT_NAME = "Reverse Nordic Curl [GHR]"
ASSIST_LADDER = [20, 15, 10, 5, 0]


def apply(db: Session) -> int:
    """Idempotent fix of the live Reverse Nordic Curl assist_ladder.

    Returns 1 if changed, 0 if already at the target value.
    """
    mv = db.exec(select(Movement).where(Movement.name == MOVEMENT_NAME)).first()
    if mv is None:
        raise ValueError(
            f"HALT-AND-FLAG: movement {MOVEMENT_NAME!r} not found in the DB. "
            "Nothing to fix — check the DB is the intended target before re-running."
        )
    if mv.assist_ladder == ASSIST_LADDER:
        print(f"{MOVEMENT_NAME}: assist_ladder already correct, no-op.")
        return 0
    mv.assist_ladder = list(ASSIST_LADDER)
    db.add(mv)
    db.commit()
    print(f"{MOVEMENT_NAME}: assist_ladder set to {ASSIST_LADDER}.")
    return 1


def main() -> None:
    from ironlog.db import engine
    with Session(engine) as db:
        apply(db)


if __name__ == "__main__":
    main()
