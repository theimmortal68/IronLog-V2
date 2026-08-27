"""belt_squat_rule_fix.py - one-off idempotent live-DB fix.

Belt Squat was wired to REP_LADDER in the authoritative Phase 1 YAML, but its
Movement.rep_ladder is not seeded and the REP_LADDER rule only mutates
MovementState.current_rep_target. That left the live D2 Belt Squat state stuck
on active_rule=REP_LADDER with no load progression.

The source YAML now maps Belt Squat to RPE_8_STANDARD. This module is the
one-shot live-data companion: it first reuses rule_wiring's normal idempotent
Movement.progression_rule update path, then updates every existing
MovementState row for Belt Squat by movement_id so active_rule matches.

Scope is intentionally narrow: only Belt Squat's progression rule fields are
corrected. Load, increment tier, rep target, streaks, cap, and rep_ladder are
left untouched.

Idempotent: safe to re-run; rows already at the target rule are no-ops.
NO from __future__ import annotations.
"""
from typing import Dict

from sqlmodel import Session, select

from ironlog.generation.rule_wiring import wire_progression_rules
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

MOVEMENT_NAME = "Belt Squat [GHR + FT]"
TARGET_RULE = ProgressionRule.RPE_8_STANDARD.value


def apply(db: Session) -> Dict[str, int]:
    """Idempotent fix of Belt Squat's live Movement + MovementState rules.

    Returns counts for the normal Movement.progression_rule wiring pass and the
    Belt Squat MovementState rows changed by this module.
    """
    wiring_counts = wire_progression_rules(db)

    mv = db.exec(select(Movement).where(Movement.name == MOVEMENT_NAME)).first()
    if mv is None:
        raise ValueError(
            f"HALT-AND-FLAG: movement {MOVEMENT_NAME!r} not found in the DB. "
            "Nothing to fix - check the DB is the intended target before re-running."
        )
    if mv.progression_rule != TARGET_RULE:
        raise ValueError(
            f"HALT-AND-FLAG: normal rule wiring left {MOVEMENT_NAME!r} at "
            f"{mv.progression_rule!r}, expected {TARGET_RULE!r}. Check the YAML "
            "and rule_wiring mapping before applying a workaround."
        )

    states = db.exec(
        select(MovementState).where(MovementState.movement_id == mv.id)
    ).all()
    states_changed = 0
    for st in states:
        if st.active_rule == TARGET_RULE:
            continue
        st.active_rule = TARGET_RULE
        db.add(st)
        states_changed += 1

    db.commit()
    return {
        "movement_rules_changed": wiring_counts["changed"],
        "movement_rules_total": wiring_counts["total"],
        "states_changed": states_changed,
        "states_total": len(states),
    }


def main() -> None:
    """Apply the Belt Squat rule fix to the live DB (idempotent). BUILD-AND-TEST-
    ONLY project convention; run from the repo root after review:

        python -m ironlog.generation.belt_squat_rule_fix
    """
    from ironlog.db import engine
    with Session(engine) as db:
        counts = apply(db)
    print("Belt Squat rule fix applied to live DB.")
    print(f"  Movement.progression_rule changed: "
          f"{counts['movement_rules_changed']} / {counts['movement_rules_total']}")
    print(f"  MovementState.active_rule changed: "
          f"{counts['states_changed']} / {counts['states_total']}")


if __name__ == "__main__":
    main()
