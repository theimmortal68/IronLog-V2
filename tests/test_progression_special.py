from ironlog.engine.advance import advance, SessionPerf
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def test_hip_thrust_advances_at_rpe9_rule_driven():
    mv = Movement(name="Hip Thrust", pattern="hinge", increment_ladder=[5,5,5], cap=220)
    st = MovementState(movement_id=1, day_id="d2", current_increment_tier=0, current_load=180)
    r = advance(ProgressionRule.RULE_DRIVEN, st,
                SessionPerf(hit_target=True, max_rpe=9.0, all_sides_cleared=True, session_performed=True), mv, 1)
    # RPE-exempt: RPE 9 still advances. Re-pointed (K2): a clean advance earns a
    # load step (increment_ladder[0]=5) and leaves the step-size tier untouched.
    assert r.advanced is True and r.new_tier is None and r.earned_load_step == 5

def test_rule_driven_advances_every_session_regardless_of_passed_window():
    # Below cap: RULE_DRIVEN must advance every session (spec §1.3), even for
    # a movement whose confirmation_window resolves to 2 (i.e. NOT tier T1) —
    # the pre-cap tier-advance branch hardcodes window=1 internally, mirroring
    # _single_session's own hardcoded gate.
    mv = Movement(name="Some Non-T1 RULE_DRIVEN Movement", pattern="hinge",
                   increment_ladder=[5,5,5], cap=300)
    st = MovementState(movement_id=1, day_id="d2", current_increment_tier=0, current_load=100)
    r = advance(ProgressionRule.RULE_DRIVEN, st,
                SessionPerf(hit_target=True, max_rpe=7.0, all_sides_cleared=True, session_performed=True),
                mv, 2)
    # Re-pointed (K2): earns the load step, does not bump the step-size tier.
    assert r.advanced is True and r.new_tier is None and r.earned_load_step == 5

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
    # one clean last set at RPE 8 -> advance. Re-pointed (K2): earns the load step
    # (increment_ladder[0]=5), step-size tier untouched.
    assert r.advanced is True and r.new_tier is None and r.earned_load_step == 5

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
