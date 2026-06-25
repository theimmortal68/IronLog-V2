"""Tests for ironlog.engine.analysis.analyze_session (v0.4 analysis hook).

Pure-core tests — no DB. The applier (persistence/apply.py) is tested
separately in tests/test_apply_analysis.py.
"""
from ironlog.models.enums import FeedbackTap, Objective, Phase
from ironlog.engine.analysis import (
    AnalysisContext, AnalysisResult, EngineStateInput, LoggedSet,
    MovementAnalysisInput, MovementStateDelta, analyze_session,
)


# ---------------------------------------------------------------------------
# Factory helpers — module-level functions, shared by every test.
# ---------------------------------------------------------------------------

def make_logged_set(
    *,
    actual_load=None, actual_reps=None, feedback_tap=None, is_warmup=False,
    target_rpe=None, target_reps_low=None, target_reps_high=None,
) -> LoggedSet:
    return LoggedSet(
        actual_load=actual_load, actual_reps=actual_reps,
        feedback_tap=feedback_tap, is_warmup=is_warmup,
        target_rpe=target_rpe, target_reps_low=target_reps_low,
        target_reps_high=target_reps_high,
    )


def make_movement_input(
    movement_id=1, *, objective=Objective.PROGRESS, current_tier=0,
    increment_ladder_len=3, consecutive_ceiling_sessions=0,
    consecutive_failed_progressions=0, logged_sets=None,
) -> MovementAnalysisInput:
    return MovementAnalysisInput(
        movement_id=movement_id, objective=objective, current_tier=current_tier,
        increment_ladder_len=increment_ladder_len,
        consecutive_ceiling_sessions=consecutive_ceiling_sessions,
        consecutive_failed_progressions=consecutive_failed_progressions,
        logged_sets=list(logged_sets or []),
    )


def make_engine_state(
    *, current_phase=Phase.CUT, bodyweight=None,
    cut_to_stab_target=213.0, cut_to_stab_tolerance=2.0,
    rhr_down=False, sleep_ok=False, no_rpe_creep=False,
    bw_stable_2wk=False, strength_bounce=False, subjective_ok=False,
) -> EngineStateInput:
    return EngineStateInput(
        current_phase=current_phase, bodyweight=bodyweight,
        cut_to_stab_target=cut_to_stab_target,
        cut_to_stab_tolerance=cut_to_stab_tolerance,
        rhr_down=rhr_down, sleep_ok=sleep_ok, no_rpe_creep=no_rpe_creep,
        bw_stable_2wk=bw_stable_2wk, strength_bounce=strength_bounce,
        subjective_ok=subjective_ok,
    )


def make_context(movements=None, engine_state=None) -> AnalysisContext:
    return AnalysisContext(
        movements=list(movements or []),
        engine_state=engine_state or make_engine_state(),
    )


def _delta_for(result: AnalysisResult, movement_id: int) -> MovementStateDelta:
    return next(d for d in result.movement_deltas if d.movement_id == movement_id)


# ---------------------------------------------------------------------------
# Task 2 — e1RM update (always-on)
# ---------------------------------------------------------------------------

def test_e1rm_from_single_working_set():
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
    ])
    result = analyze_session(make_context([mv]))
    delta = _delta_for(result, 1)
    # estimate_e1rm(100, 5, 8.0, ON_TARGET) — assert it's a positive float > the raw load
    assert delta.new_e1rm is not None
    assert delta.new_e1rm > 100.0


def test_e1rm_is_max_across_working_sets():
    # A heavier top set and a lighter back-off; the top set must win.
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=80.0,  actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=7.0),
    ])
    from ironlog.engine.e1rm import estimate_e1rm
    expected = max(
        estimate_e1rm(100.0, 5, 8.0, FeedbackTap.ON_TARGET),
        estimate_e1rm(80.0, 8, 7.0, FeedbackTap.ON_TARGET),
    )
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm == expected


def test_fatigued_late_set_does_not_drag_e1rm_down():
    # A strong top set then a fatigued set-3 with fewer reps; e1RM tracks the top.
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=200.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=200.0, actual_reps=2, feedback_tap=FeedbackTap.TOO_HARD, target_rpe=8.0),
    ])
    from ironlog.engine.e1rm import estimate_e1rm
    top = estimate_e1rm(200.0, 5, 8.0, FeedbackTap.ON_TARGET)
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm == top  # the 5-rep set, not the fatigued 2-rep set


def test_no_qualifying_sets_leaves_e1rm_untouched():
    # All warmups / missing data → no anchor → new_e1rm is None.
    mv = make_movement_input(logged_sets=[
        make_logged_set(actual_load=45.0, actual_reps=10, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0, is_warmup=True),
        make_logged_set(actual_load=None, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=100.0, actual_reps=None, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=None, target_rpe=8.0),       # no tap
        make_logged_set(actual_load=100.0, actual_reps=5, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=None),  # no rpe
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm is None


# ---------------------------------------------------------------------------
# Task 3 — outcome determination, objective gating, tier step-down
# ---------------------------------------------------------------------------

def _progress_mv(**kw):
    """A PROGRESS movement with a 3-rung ladder, defaults overridable."""
    kw.setdefault("increment_ladder_len", 3)
    return make_movement_input(objective=Objective.PROGRESS, **kw)


def test_ceiling_increments_and_resets_failed():
    mv = _progress_mv(consecutive_ceiling_sessions=1, consecutive_failed_progressions=2, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # reps>=high, not TOO_HARD
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling == 2   # 1 + 1
    assert delta.new_consecutive_failed == 0     # reset
    assert delta.new_tier is None                # ceiling does not step tier


def test_miss_via_low_reps_increments_failed_and_resets_ceiling():
    mv = _progress_mv(consecutive_ceiling_sessions=2, consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # reps<low
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_failed == 1     # 0 + 1
    assert delta.new_consecutive_ceiling == 0     # reset


def test_miss_via_too_hard_even_when_reps_in_range():
    mv = _progress_mv(consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=6, feedback_tap=FeedbackTap.TOO_HARD,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # in-range but TOO_HARD
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_failed == 1
    assert delta.new_consecutive_ceiling == 0


def test_neither_resets_both_counters():
    mv = _progress_mv(consecutive_ceiling_sessions=2, consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=6, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # in-range, on-target
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling == 0
    assert delta.new_consecutive_failed == 0


def test_neither_when_anchor_lacks_rep_range():
    # No rep range and not TOO_HARD → can't classify hit/miss → NEITHER (both reset).
    mv = _progress_mv(consecutive_ceiling_sessions=2, consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=6, feedback_tap=FeedbackTap.ON_TARGET, target_rpe=8.0),
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling == 0
    assert delta.new_consecutive_failed == 0


def test_mutual_exclusivity_never_ticks_both():
    # Whatever the outcome, a ceiling increment and a failed increment never co-occur.
    for tap, reps in [(FeedbackTap.ON_TARGET, 8), (FeedbackTap.ON_TARGET, 3), (FeedbackTap.TOO_HARD, 6)]:
        mv = _progress_mv(consecutive_ceiling_sessions=5, consecutive_failed_progressions=5, logged_sets=[
            make_logged_set(actual_load=100.0, actual_reps=reps, feedback_tap=tap,
                            target_rpe=8.0, target_reps_low=5, target_reps_high=8),
        ])
        delta = _delta_for(analyze_session(make_context([mv])), 1)
        incremented = sum([
            delta.new_consecutive_ceiling == 6,
            delta.new_consecutive_failed == 6,
        ])
        assert incremented <= 1, f"both ticked for tap={tap} reps={reps}"


def test_maintain_leaves_counters_and_tier_untouched_but_updates_e1rm():
    mv = make_movement_input(objective=Objective.MAINTAIN, consecutive_ceiling_sessions=1,
                             consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=8, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # would be CEILING if PROGRESS
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_consecutive_ceiling is None   # untouched in MAINTAIN
    assert delta.new_consecutive_failed is None
    assert delta.new_tier is None
    assert delta.new_e1rm is not None              # measurement still updates


def test_second_consecutive_miss_steps_tier_down_and_resets_failed():
    mv = _progress_mv(current_tier=0, increment_ladder_len=3,
                      consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # MISS → failed 1→2
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_tier == 1                    # step_down_tier(0, 3, 2) == 1
    assert delta.new_consecutive_failed == 0       # reset after the drop


def test_first_miss_does_not_step_tier():
    mv = _progress_mv(current_tier=0, increment_ladder_len=3,
                      consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # MISS → failed 0→1
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_tier is None                 # step_down_tier(0, 3, 1) == 0 (unchanged)
    assert delta.new_consecutive_failed == 1


def test_tier_step_down_respects_ladder_bound_at_finest_rung():
    # Already at the finest rung (tier == ladder_len - 1) → no drop even at threshold.
    mv = _progress_mv(current_tier=2, increment_ladder_len=3,
                      consecutive_failed_progressions=1, logged_sets=[
        make_logged_set(actual_load=100.0, actual_reps=3, feedback_tap=FeedbackTap.ON_TARGET,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),  # MISS → failed 1→2
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_tier is None                 # step_down_tier(2, 3, 2) == 2 (unchanged) → None
    assert delta.new_consecutive_failed == 2       # counter still ticks; no drop available


def test_combined_too_hard_anchor_updates_e1rm_and_misses():
    # The decoupling proof: anchor set is TOO_HARD → e1RM still records AND outcome is MISS.
    mv = _progress_mv(consecutive_failed_progressions=0, logged_sets=[
        make_logged_set(actual_load=200.0, actual_reps=4, feedback_tap=FeedbackTap.TOO_HARD,
                        target_rpe=8.0, target_reps_low=5, target_reps_high=8),
    ])
    delta = _delta_for(analyze_session(make_context([mv])), 1)
    assert delta.new_e1rm is not None             # measurement records from the same anchor
    assert delta.new_consecutive_failed == 1       # prescription: MISS
