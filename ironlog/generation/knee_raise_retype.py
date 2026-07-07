"""knee_raise_retype.py — live-DB data fix (Fix C): retype `Face-Up Incline
Knee Raise` from a mis-typed LADDER/lb movement to the correct bodyweight
ASSISTED/incline-degrees movement.

Athlete feedback: knee raises are bodyweight; the only intensity lever is the
incline angle, which belongs on `MovementState.assist_level` (degrees), not
`current_load` (lb). The movement previously shipped `progression_mode=LADDER`
with `current_load=25` (D1) / `10` (D4) seeded in lb, so the app showed "25 lb".
A from-scratch DB now seeds this movement correctly (see `ironlog/seed.py` +
`ironlog/generation/baseline_seed.py`); this module is the one-shot fix for an
EXISTING (already-seeded) live DB that still carries the old mis-typed rows.

Idempotent UPDATE, modeled on `rule_wiring.py`:
  - `retype_knee_raise(db)` — safe to re-run; rows already at the target state
    are left alone and not counted as "changed".
  - `main()` — apply to the live DB via `python -m ironlog.generation.knee_raise_retype`.

Scope is intentionally narrow: only `Face-Up Incline Knee Raise`'s Movement row
and its own MovementState rows are touched. Nordic Curl [GHR] / Reverse Nordic
Curl [GHR] are already correctly configured and this module never looks at them.

BUILD-AND-TEST-ONLY project convention — do NOT run main() against the live DB
without review/backup; the orchestrator applies after review.

NO from __future__ import annotations (project-wide constraint).
"""
from typing import Dict

from sqlmodel import Session, select

from ironlog.models.enums import ProgressionMode
from ironlog.models.library import Movement, MovementState

MOVEMENT_NAME = "Face-Up Incline Knee Raise"
ASSIST_LADDER = [25, 20, 15, 10, 5, 0]


def retype_knee_raise(db: Session) -> Dict[str, int]:
    """Idempotent fix of the live `Face-Up Incline Knee Raise` Movement +
    MovementState rows.

    Movement: progression_mode -> ASSISTED, assist_ladder -> the full incline
    ladder (both unconditional-but-safe: re-running with the target state
    already in place makes no changes and isn't counted).

    MovementState: for every row belonging to this movement whose
    `current_load` is not None, move that value onto `assist_level` (only
    when `assist_level` is not already set — never clobber a value the engine
    has since progressed) and clear `current_load`. A row that has already
    been migrated (current_load is None) is left untouched, making repeated
    runs a no-op.

    Halt-and-flag (mirrors rule_wiring.wire_progression_rules): raises if the
    movement is not present in the DB — never invent, never silently skip.

    Returns {"movement_changed": 0|1, "states_changed": n, "states_total": m}.
    """
    mv = db.exec(select(Movement).where(Movement.name == MOVEMENT_NAME)).first()
    if mv is None:
        raise ValueError(
            f"HALT-AND-FLAG: movement {MOVEMENT_NAME!r} not found in the DB. "
            "Nothing to retype — check the DB is the intended target before re-running."
        )

    movement_changed = 0
    if mv.progression_mode != ProgressionMode.ASSISTED or mv.assist_ladder != ASSIST_LADDER:
        mv.progression_mode = ProgressionMode.ASSISTED
        mv.assist_ladder = list(ASSIST_LADDER)
        db.add(mv)
        movement_changed = 1

    states = db.exec(select(MovementState).where(MovementState.movement_id == mv.id)).all()
    states_changed = 0
    for st in states:
        if st.current_load is None:
            continue   # already migrated (or never carried a load) — no-op
        if st.assist_level is None:
            st.assist_level = st.current_load
        st.current_load = None
        db.add(st)
        states_changed += 1

    db.commit()
    return {
        "movement_changed": movement_changed,
        "states_changed": states_changed,
        "states_total": len(states),
    }


def main() -> None:
    """Apply the retype to the live DB (idempotent). BUILD-AND-TEST-ONLY project
    convention; run from the repo root after review (backup taken):

        python -m ironlog.generation.knee_raise_retype
    """
    from ironlog.db import engine
    with Session(engine) as db:
        counts = retype_knee_raise(db)
    print("Face-Up Incline Knee Raise retype applied to live DB.")
    print(f"  Movement.progression_mode/assist_ladder changed: {counts['movement_changed']}")
    print(f"  MovementState rows migrated (current_load -> assist_level): "
          f"{counts['states_changed']} / {counts['states_total']}")


if __name__ == "__main__":
    main()
