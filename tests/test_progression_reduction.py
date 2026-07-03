from ironlog.engine.advance import advance, SessionPerf, roll_unassisted_max
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def test_incline_reduction_two_session_steps_down_ladder():
    mv = Movement(name="Nordic", pattern="hinge", assist_ladder=[20,15,10,5,0])
    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)
    st1 = MovementState(movement_id=1, day_id="d2", assist_level=20, consecutive_advance_count=0)
    r1 = advance(ProgressionRule.INCLINE_REDUCTION, st1, perf, mv, 2)
    assert r1.advanced is False and r1.consecutive_advance_count == 1
    st2 = MovementState(movement_id=1, day_id="d2", assist_level=20, consecutive_advance_count=1)
    r2 = advance(ProgressionRule.INCLINE_REDUCTION, st2, perf, mv, 2)
    assert r2.advanced is True and r2.new_assist_level == 15

def test_body_position_steps_tuck_to_single_leg():
    mv = Movement(name="Dragon Flag", pattern="core", position_ladder=["tuck","single_leg_extended","straddle","full"])
    st = MovementState(movement_id=1, day_id="d4", current_body_position="tuck", consecutive_advance_count=1)
    r = advance(ProgressionRule.BODY_POSITION, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True), mv, 2)
    assert r.advanced is True and r.new_body_position == "single_leg_extended"

def test_unilateral_one_side_fails_no_advance():
    mv = Movement(name="ATG Split Squat", pattern="squat", increment_ladder=[2.5])
    r = advance(ProgressionRule.RPE_8_STANDARD, MovementState(movement_id=1, day_id="d2", consecutive_advance_count=0),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=False), mv, 1)  # one side failed
    assert r.advanced is False

def test_pull_up_rolling_max_tracked_no_cross_day_action():
    mv = Movement(name="Pull-up", pattern="pull")
    st = MovementState(movement_id=1, day_id="d4", unassisted_max_rolling=5)
    r = advance(ProgressionRule.PULL_UP_ROLLING_MAX, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True, unassisted_set1_reps=6), mv, 1)
    assert r.advanced is False   # tracking-only this chunk; the CALLER updates unassisted_max_rolling (see Step 2)

def test_assistance_reduction_reaches_terminal_and_hands_off_to_rpe8_standard():
    mv = Movement(name="Assisted Pull-up", pattern="pull", assist_ladder=[40,20,0])
    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)
    st = MovementState(movement_id=1, day_id="d4", assist_level=20, consecutive_advance_count=1)
    r = advance(ProgressionRule.ASSISTANCE_REDUCTION, st, perf, mv, 2)
    assert r.advanced is True and r.new_assist_level == 0
    assert r.active_rule == ProgressionRule.RPE_8_STANDARD.value

def test_assistance_reduction_terminal_rung_holds():
    mv = Movement(name="Assisted Pull-up", pattern="pull", assist_ladder=[40,20,0])
    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)
    st = MovementState(movement_id=1, day_id="d4", assist_level=0, consecutive_advance_count=1)
    r = advance(ProgressionRule.ASSISTANCE_REDUCTION, st, perf, mv, 2)
    assert r.advanced is False and r.new_assist_level == 0

def test_body_position_terminal_rung_holds():
    mv = Movement(name="Dragon Flag", pattern="core", position_ladder=["tuck","single_leg_extended","straddle","full"])
    st = MovementState(movement_id=1, day_id="d4", current_body_position="full", consecutive_advance_count=1)
    r = advance(ProgressionRule.BODY_POSITION, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True), mv, 2)
    assert r.advanced is False and r.new_body_position == "full"

def test_incline_reduction_dirty_session_resets_streak():
    mv = Movement(name="Nordic", pattern="hinge", assist_ladder=[20,15,10,5,0])
    st = MovementState(movement_id=1, day_id="d2", assist_level=20, consecutive_advance_count=1)
    r = advance(ProgressionRule.INCLINE_REDUCTION, st,
                SessionPerf(hit_target=False, max_rpe=9.0, all_sides_cleared=True), mv, 2)
    assert r.advanced is False and r.consecutive_advance_count == 0 and r.new_assist_level == 20

def test_roll_unassisted_max_takes_the_higher_value():
    assert roll_unassisted_max(5, 6) == 6
    assert roll_unassisted_max(7, 6) == 7
    assert roll_unassisted_max(None, 6) == 6
    assert roll_unassisted_max(5, None) == 5
    assert roll_unassisted_max(None, None) == 0
