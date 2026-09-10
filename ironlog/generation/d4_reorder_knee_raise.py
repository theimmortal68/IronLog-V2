"""d4_reorder_knee_raise.py — one-off idempotent live-DB fix.

Swaps TierExercise.exercise_order for D4 T2 GS slots d4_t2b (Single-Arm DB
Row) and d4_t2c (Face-Up Incline Knee Raise) to match the reordered seed
source (Face-Up Knee now runs between Meadows Row and Single-Arm DB Row).
slot_id -> movement mapping is untouched; only the order values move.

Idempotent: safe to re-run (sets both to their target order unconditionally;
a second run is a no-op). NO from __future__ import annotations.
"""
from sqlmodel import Session, select

from ironlog.models.program import TierExercise


def apply(db: Session) -> None:
    d4_t2b = db.exec(select(TierExercise).where(TierExercise.slot_id == "d4_t2b")).one()
    d4_t2c = db.exec(select(TierExercise).where(TierExercise.slot_id == "d4_t2c")).one()
    if d4_t2b.exercise_order == 3 and d4_t2c.exercise_order == 2:
        print("d4_reorder_knee_raise: already applied, no-op.")
        return
    d4_t2b.exercise_order = 3
    d4_t2c.exercise_order = 2
    db.add(d4_t2b)
    db.add(d4_t2c)
    db.commit()
    print("d4_reorder_knee_raise: applied. d4_t2b(Single-Arm DB Row)->order 3, "
          "d4_t2c(Face-Up Incline Knee Raise)->order 2.")


def main() -> None:
    from ironlog.db import engine
    with Session(engine) as db:
        apply(db)


if __name__ == "__main__":
    main()
