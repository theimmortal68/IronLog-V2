"""nordic_curl_ladder_fix.py - one-off idempotent live-DB fix.

Movement.assist_ladder was never populated for "Nordic Curl [GHR]" (a gap in
ironlog/seed.py's master movement definitions - no assist_ladder key was ever
included for any ASSISTED movement). This left the movement's progression_rule
(INCLINE_REDUCTION, already correctly set) unable to advance: advance.py's
_incline_reduction reads `movement.assist_ladder or []`, so an empty ladder
means the assist level can never step down (lower incline = harder is the
correct semantics; the movement just could never progress toward it).

Scope is intentionally narrow: only this one movement's assist_ladder is
touched. Nordic Curl - Volume is unused in the live program and Pull-up uses
PULL_UP_ROLLING_MAX rather than an assist ladder.

Idempotent: safe to re-run (sets assist_ladder unconditionally to the target
value; a second run makes no changes and isn't counted).
NO from __future__ import annotations.
"""
from sqlmodel import Session, select

from ironlog.models.library import Movement

MOVEMENT_NAME = "Nordic Curl [GHR]"
ASSIST_LADDER = [25, 20, 15, 10, 5, 0]


def apply(db: Session) -> int:
    """Idempotent fix of the live Nordic Curl assist_ladder.

    Returns 1 if changed, 0 if already at the target value.
    """
    mv = db.exec(select(Movement).where(Movement.name == MOVEMENT_NAME)).first()
    if mv is None:
        raise ValueError(
            f"HALT-AND-FLAG: movement {MOVEMENT_NAME!r} not found in the DB. "
            "Nothing to fix - check the DB is the intended target before re-running."
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
