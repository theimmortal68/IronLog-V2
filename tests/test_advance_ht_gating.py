from ironlog.engine.advance import SessionPerf, advance
from ironlog.engine.band_composite import Band, ht_next_setup
from ironlog.models.enums import LiftCategory, ProgressionMode, ProgressionRule
from ironlog.models.library import Movement, MovementState


INVENTORY = [
    Band(1, 18.0, 45.0, True),
    Band(2, 36.0, 90.0, True),
]


def _ht_movement():
    return Movement(
        id=1,
        name="Hip Thrust [HIP_THRUST]",
        base_name="Hip Thrust",
        lift_category=LiftCategory.HIP_THRUST,
        progression_mode=ProgressionMode.COMPOSITE,
        progression_rule=ProgressionRule.RULE_DRIVEN.value,
        rep_ladder=[8, 10, 12],
        cap=220.0,
    )


def _ht_state():
    return MovementState(
        movement_id=1,
        day_id="D2 Lower A",
        ht_plates=205.0,
        ht_band_config=[1],
        consecutive_advance_count=2,
    )


def _perf(*, hit=True, rpe=8.0, performed=True):
    return SessionPerf(
        hit_target=hit,
        max_rpe=rpe,
        all_sides_cleared=True,
        session_performed=performed,
    )


def test_clean_ht_session_earns_staged_next_setup():
    state = _ht_state()
    expected = ht_next_setup(state.ht_plates, state.ht_band_config, INVENTORY)

    result = advance(
        ProgressionRule.RULE_DRIVEN,
        state,
        _perf(),
        _ht_movement(),
        confirmation_window=1,
        band_inventory=INVENTORY,
    )

    assert result.advanced is True
    assert result.active_rule == ProgressionRule.RULE_DRIVEN.value
    assert result.consecutive_advance_count == 0
    assert result.earned_ht_plates == expected[0]
    assert result.earned_ht_band_config == list(expected[1])
    assert result.earned_load_step is None
    assert result.new_rep_target is None


def test_dirty_ht_session_holds_without_earned_setup():
    result = advance(
        ProgressionRule.RULE_DRIVEN,
        _ht_state(),
        _perf(rpe=9.0),
        _ht_movement(),
        confirmation_window=1,
        band_inventory=INVENTORY,
    )

    assert result.advanced is False
    assert result.active_rule == ProgressionRule.RULE_DRIVEN.value
    assert result.consecutive_advance_count == 2
    assert result.earned_ht_plates is None
    assert result.earned_ht_band_config is None


def test_unperformed_ht_session_holds_without_resetting_streak():
    result = advance(
        ProgressionRule.RULE_DRIVEN,
        _ht_state(),
        _perf(performed=False),
        _ht_movement(),
        confirmation_window=1,
        band_inventory=INVENTORY,
    )

    assert result.advanced is False
    assert result.consecutive_advance_count == 2
    assert result.earned_ht_plates is None


def test_ht_without_band_inventory_is_safe_hold():
    result = advance(
        ProgressionRule.RULE_DRIVEN,
        _ht_state(),
        _perf(),
        _ht_movement(),
        confirmation_window=1,
        band_inventory=None,
    )

    assert result.advanced is False
    assert result.consecutive_advance_count == 2
    assert result.earned_ht_plates is None

