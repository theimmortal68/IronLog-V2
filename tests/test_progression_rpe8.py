from ironlog.engine.advance import advance, SessionPerf, AdvanceResult
from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement, MovementState

def _mv(): return Movement(name="Bench", pattern="press", increment_ladder=[5, 5, 5])
def _st(tier=0, streak=0): return MovementState(movement_id=1, day_id="d1",
    current_increment_tier=tier, consecutive_advance_count=streak)

# _mv() has increment_ladder=[5, 5, 5]; a clean advance at tier 0 earns step 5.0.
# Re-pointed (K2): these previously asserted new_tier == 1 on a clean advance,
# which encoded the step-size-index bug — a clean advance EARNS a load step
# (earned_load_step) and must leave the tier untouched (new_tier is None).

def test_t1_advances_in_one_clean_session():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=0),
                SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True), _mv(), confirmation_window=1)
    assert r.advanced is True and r.new_tier is None and r.consecutive_advance_count == 0
    assert r.earned_load_step == 5   # increment_ladder[0]

def test_t2_needs_two_clean_sessions():
    perf = SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True)
    r1 = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=0), perf, _mv(), confirmation_window=2)
    assert r1.advanced is False and r1.consecutive_advance_count == 1
    r2 = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=1), perf, _mv(), confirmation_window=2)
    assert r2.advanced is True and r2.new_tier is None and r2.consecutive_advance_count == 0
    assert r2.earned_load_step == 5

def test_streak_resets_on_missed_reps():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=1),
                SessionPerf(hit_target=False, max_rpe=8.0, all_sides_cleared=True), _mv(), confirmation_window=2)
    assert r.advanced is False and r.consecutive_advance_count == 0

def test_no_advance_when_rpe_over_8():
    r = advance(ProgressionRule.RPE_8_STANDARD, _st(streak=1),
                SessionPerf(hit_target=True, max_rpe=9.0, all_sides_cleared=True), _mv(), confirmation_window=2)
    assert r.advanced is False and r.consecutive_advance_count == 0
