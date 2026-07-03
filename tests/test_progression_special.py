from ironlog.engine.advance import advance, SessionPerf
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def test_hip_thrust_advances_at_rpe9_rule_driven():
    mv = Movement(name="Hip Thrust", pattern="hinge", increment_ladder=[5,5,5], cap=220)
    st = MovementState(movement_id=1, day_id="d2", current_increment_tier=0, current_load=180)
    r = advance(ProgressionRule.RULE_DRIVEN, st,
                SessionPerf(hit_target=True, max_rpe=9.0, all_sides_cleared=True, session_performed=True), mv, 1)
    assert r.advanced is True and r.new_tier == 1   # RPE-exempt: RPE 9 still advances

def test_hip_thrust_transitions_to_rep_ladder_at_cap():
    mv = Movement(name="Hip Thrust", pattern="hinge", increment_ladder=[5], cap=220, rep_ladder=[8,10,12])
    st = MovementState(movement_id=1, day_id="d2", current_load=220, current_increment_tier=0)  # at cap
    r = advance(ProgressionRule.RULE_DRIVEN, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True, session_performed=True), mv, 1)
    assert r.active_rule == ProgressionRule.REP_LADDER.value   # rule transitions at ceiling

def test_belt_squat_rep_ladder_advances_reps_two_session():
    mv = Movement(name="Belt Squat", pattern="squat", rep_ladder=[8,10,12,15], cap=260)
    st = MovementState(movement_id=1, day_id="d2", current_load=260, current_rep_target=8, consecutive_advance_count=1)
    r = advance(ProgressionRule.REP_LADDER, st,
                SessionPerf(hit_target=True, max_rpe=7.0, all_sides_cleared=True, session_performed=True), mv, 2)
    assert r.advanced is True and r.new_rep_target == 10

def test_vbar_single_session_advances_on_clean_last_set():
    mv = Movement(name="V-Bar Pushdown", pattern="press", increment_ladder=[5,5,5], cap=200)
    st = MovementState(movement_id=1, day_id="d4", current_increment_tier=0)
    r = advance(ProgressionRule.SINGLE_SESSION, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True, last_set_hit_target=True), mv, 1)
    assert r.advanced is True and r.new_tier == 1   # one clean last set at RPE 8 -> advance

def test_vbar_single_session_no_advance_when_last_set_missed():
    mv = Movement(name="V-Bar Pushdown", pattern="press", increment_ladder=[5,5,5], cap=200)
    st = MovementState(movement_id=1, day_id="d4", current_increment_tier=0)
    r = advance(ProgressionRule.SINGLE_SESSION, st,
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True, last_set_hit_target=False), mv, 1)
    assert r.advanced is False

def test_vbar_single_session_no_advance_when_rpe_over_8():
    mv = Movement(name="V-Bar Pushdown", pattern="press", increment_ladder=[5,5,5], cap=200)
    st = MovementState(movement_id=1, day_id="d4", current_increment_tier=0)
    r = advance(ProgressionRule.SINGLE_SESSION, st,
                SessionPerf(hit_target=True, max_rpe=9.0, all_sides_cleared=True, last_set_hit_target=True), mv, 1)
    assert r.advanced is False

def test_fixed_load_never_advances():
    mv = Movement(name="Rev Hyper Recovery", pattern="hinge")
    st = MovementState(movement_id=1, day_id="d6", current_load=90)
    r = advance(ProgressionRule.FIXED_LOAD, st,
                SessionPerf(hit_target=True, max_rpe=6.0, all_sides_cleared=True, session_performed=True), mv, 2)
    assert r.advanced is False and r.new_tier is None
