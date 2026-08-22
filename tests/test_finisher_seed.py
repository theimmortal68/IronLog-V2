"""
test_finisher_seed.py — finisher schema/data seed gate.

NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import select

from ironlog.models.enums import LiftCategory, ProgressionMode, ProgressionRule
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import DayFinisher, ProgramDay


EXPECTED_FINISHERS = {
    1: ("kb_swing", {
        "weight_lb": 30,
        "work_seconds_per_minute": 40,
        "rest_seconds_per_minute": 20,
        "target_reps_per_minute": 15,
        "equipment": ["kettlebell_30"],
    }),
    2: ("sled_push", {
        "resistance_level": 8,
        "work_seconds_per_minute": 20,
        "rest_seconds_per_minute": 30,
        "equipment": ["dreadmill"],
    }),
    4: ("sandbag_load_to_utility_seat", {
        "weight_lb": 100,
        "utility_seat_height_inches": 52,
        "target_reps_per_minute": 4,
        "scheme": "emom",
        "equipment": ["sandbag_100", "utility_seat", "spotter_arms"],
    }),
    5: ("heavy_farmer_carry", {
        "weight_lb": 55,
        "work_seconds_per_minute": 40,
        "rest_seconds_per_minute": 20,
        "equipment": ["dreadmill", "farmer_handles"],
    }),
    6: ("jump_rope", {
        "rope_type": "crossrope_quarter_lb",
        "work_seconds": 20,
        "rest_seconds": 10,
        "rounds_per_block": 8,
        "blocks": 2,
        "inter_block_rest_seconds": 75,
        "work_seconds_per_minute": 30,
        "target_reps_per_minute": 40,
        "scheme": "tabata",
        "equipment": ["crossrope_quarter_lb"],
    }),
}


def test_phase1_finisher_rows_link_days_and_movements(gen_db):
    days = {d.day_index: d for d in gen_db.exec(select(ProgramDay)).all()}
    finishers = {
        f.program_day_id: f
        for f in gen_db.exec(select(DayFinisher)).all()
    }

    assert len(finishers) == 5
    assert days[3].id not in finishers
    assert days[7].id not in finishers

    for day_index, (movement_name, params) in EXPECTED_FINISHERS.items():
        finisher = finishers[days[day_index].id]
        movement = gen_db.get(Movement, finisher.movement_id)

        assert finisher.duration_minutes == 6
        assert finisher.params == params
        assert movement.name == movement_name
        assert movement.base_name == movement_name
        assert movement.lift_category == LiftCategory.NONE
        assert movement.progression_mode == ProgressionMode.FINISHER


def test_phase1_d6_finisher_duration_then_rope_state(gen_db):
    movement = gen_db.exec(
        select(Movement).where(Movement.name == "jump_rope")
    ).one()
    state = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == movement.id)
    ).one()

    assert movement.progression_rule == ProgressionRule.FINISHER_DURATION_THEN_ROPE
    assert movement.rope_ladder == ["quarter_lb", "half_lb", "one_lb"]
    assert state.active_rule == ProgressionRule.FINISHER_DURATION_THEN_ROPE
    assert state.duration_ladder == [35, 40, 45, 50]
    assert state.current_duration_seconds == 35
    assert state.current_rope == "quarter_lb"
