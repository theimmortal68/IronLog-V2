from ironlog.engine.advance import SessionPerf, advance
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState


def _clean_perf():
    return SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)


def _apply_finisher_result(state, result):
    return MovementState(
        movement_id=state.movement_id,
        day_id=state.day_id,
        duration_ladder=state.duration_ladder,
        current_duration_seconds=result.new_duration_seconds,
        current_rope=result.new_rope or state.current_rope,
        consecutive_advance_count=result.consecutive_advance_count,
    )


def test_finisher_duration_terminal_advances_rope_and_resets_duration():
    movement = Movement(
        name="jump_rope",
        pattern="finisher",
        rope_ladder=["quarter_lb", "half_lb", "one_lb"],
    )
    state = MovementState(
        movement_id=1,
        day_id="d6",
        duration_ladder=[35, 40, 45, 50],
        current_duration_seconds=35,
        current_rope="quarter_lb",
    )

    for _ in range(4):
        result = advance(
            ProgressionRule.FINISHER_DURATION_THEN_ROPE,
            state,
            _clean_perf(),
            movement,
            1,
        )
        state = _apply_finisher_result(state, result)

    assert result.advanced is True
    assert result.new_duration_seconds == 35
    assert result.new_rope == "half_lb"
    assert state.current_duration_seconds == 35
    assert state.current_rope == "half_lb"


def test_finisher_true_terminal_holds_rope_and_duration():
    movement = Movement(
        name="jump_rope",
        pattern="finisher",
        rope_ladder=["quarter_lb", "half_lb", "one_lb"],
    )
    state = MovementState(
        movement_id=1,
        day_id="d6",
        duration_ladder=[35, 40, 45, 50],
        current_duration_seconds=50,
        current_rope="one_lb",
    )

    result = advance(
        ProgressionRule.FINISHER_DURATION_THEN_ROPE,
        state,
        _clean_perf(),
        movement,
        1,
    )

    assert result.advanced is False
    assert result.new_duration_seconds == 50
    assert result.new_rope == "one_lb"
