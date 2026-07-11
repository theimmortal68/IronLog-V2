"""live_seed_ramp_and_finishers.py — one-off idempotent live-DB seed.

Applies the two pieces of seed data that specs 04/05a-05c's schema
migrations (025/026) support but never populate themselves: the
ramp_eligible flag on the 6 heavy-barbell anchor movements, and the
5 finisher Movement/MovementState/DayFinisher rows. Mirrors
program_seed.py's _mark_ramp_eligible_movements/_seed_finishers logic,
but idempotent against an already-seeded production DB (that module's
seed_phase1_program() recreates Program/ProgramDay/Tier structure from
scratch and must NEVER run against production).

NO from __future__ import annotations.
"""
from sqlmodel import Session, select

from ironlog.generation.program_seed import RAMP_ELIGIBLE_MOVEMENT_NAMES
from ironlog.models.enums import LiftCategory, ProgressionMode, ProgressionRule
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import DayFinisher, ProgramDay

FINISHER_SPECS = {
    1: {
        "name": "kb_swing",
        "params": {"weight_lb": 30, "target_reps_per_minute": 15, "equipment": ["kettlebell_30"]},
    },
    2: {
        "name": "sled_push",
        "params": {"resistance_level": 8, "work_seconds_per_minute": 30, "equipment": ["dreadmill"]},
    },
    4: {
        "name": "sandbag_load_to_utility_seat",
        "params": {
            "weight_lb": 100, "utility_seat_height_inches": 52,
            "target_reps_per_minute": 4,
            "equipment": ["sandbag_100", "utility_seat", "spotter_arms"],
        },
    },
    5: {
        "name": "heavy_farmer_carry",
        "params": {
            "weight_lb": 55, "work_seconds_per_minute": 40, "rest_seconds_per_minute": 20,
            "equipment": ["dreadmill", "farmer_handles"],
        },
    },
    6: {
        "name": "jump_rope",
        "params": {
            "rope_type": "crossrope_quarter_lb", "work_seconds_per_minute": 30,
            "target_reps_per_minute": 40, "equipment": ["crossrope_quarter_lb"],
        },
        "duration_ladder": [35, 40, 45, 50],
        "rope_ladder": ["quarter_lb", "half_lb", "one_lb"],
        "current_duration_seconds": 35,
        "current_rope": "quarter_lb",
    },
}


def apply(db: Session) -> None:
    _mark_ramp_eligible(db)
    _seed_finishers(db)


def _mark_ramp_eligible(db: Session) -> None:
    movements = db.exec(
        select(Movement).where(Movement.name.in_(RAMP_ELIGIBLE_MOVEMENT_NAMES))
    ).all()
    found = {m.name for m in movements}
    missing = sorted(RAMP_ELIGIBLE_MOVEMENT_NAMES - found)
    if missing:
        raise ValueError(f"HALT-AND-FLAG: ramp-eligible movement(s) missing from live DB: {missing!r}")
    changed = 0
    for m in movements:
        if not m.ramp_eligible:
            m.ramp_eligible = True
            db.add(m)
            changed += 1
    db.commit()
    print(f"ramp_eligible: {changed} movement(s) updated, {len(movements) - changed} already set.")


def _seed_finishers(db: Session) -> None:
    days_by_index = {
        pd.day_index: pd
        for pd in db.exec(select(ProgramDay)).all()
    }
    existing_names = {m.name for m in db.exec(select(Movement)).all()}
    created = 0
    for day_index, spec in FINISHER_SPECS.items():
        if spec["name"] in existing_names:
            print(f"finisher '{spec['name']}' (day {day_index}): already seeded, skipping.")
            continue
        if day_index not in days_by_index:
            raise ValueError(f"HALT-AND-FLAG: no ProgramDay with day_index={day_index} in live DB.")

        movement = Movement(
            name=spec["name"],
            base_name=spec["name"],
            lift_category=LiftCategory.NONE,
            progression_mode=ProgressionMode.FINISHER,
            progression_rule=(
                ProgressionRule.FINISHER_DURATION_THEN_ROPE if day_index == 6 else None
            ),
            rope_ladder=spec.get("rope_ladder"),
        )
        db.add(movement)
        db.flush()

        db.add(MovementState(
            movement_id=movement.id,
            active_rule=(
                ProgressionRule.FINISHER_DURATION_THEN_ROPE if day_index == 6 else None
            ),
            duration_ladder=spec.get("duration_ladder"),
            current_duration_seconds=spec.get("current_duration_seconds"),
            current_rope=spec.get("current_rope"),
        ))
        db.add(DayFinisher(
            program_day_id=days_by_index[day_index].id,
            movement_id=movement.id,
            duration_minutes=6,
            params=spec["params"],
        ))
        created += 1
    db.commit()
    print(f"finishers: {created} new finisher(s) seeded, {len(FINISHER_SPECS) - created} already present.")


def main() -> None:
    from ironlog.db import engine
    with Session(engine) as db:
        apply(db)


if __name__ == "__main__":
    main()
