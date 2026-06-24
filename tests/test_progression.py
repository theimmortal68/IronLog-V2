from ironlog.engine import (
    resolve_objective, should_attempt_progression, step_down_tier,
    reset_tier_on_rebuild, maybe_reset_tier_on_breakthrough,
)
from ironlog.models import Objective


def test_pullup_overrides_to_progress_in_cut():
    # phase default MAINTAIN, movement override PROGRESS -> PROGRESS
    assert resolve_objective(Objective.PROGRESS, Objective.MAINTAIN) == Objective.PROGRESS


def test_primary_inherits_phase_default():
    assert resolve_objective(None, Objective.MAINTAIN) == Objective.MAINTAIN


def test_progression_gating():
    assert should_attempt_progression(Objective.PROGRESS, False) is True
    assert should_attempt_progression(Objective.MAINTAIN, False) is False
    assert should_attempt_progression(Objective.MAINTAIN, True) is True   # REBUILD


def test_tier_steps_down_after_two_fails():
    assert step_down_tier(0, 3, consecutive_fails=2) == 1
    assert step_down_tier(0, 3, consecutive_fails=1) == 0
    assert step_down_tier(2, 3, consecutive_fails=2) == 2   # already at finest


def test_tier_reset_rebuild_and_breakthrough():
    assert reset_tier_on_rebuild() == 0
    assert maybe_reset_tier_on_breakthrough(2, e1rm_gain=12, coarse_step=10) == 1
    assert maybe_reset_tier_on_breakthrough(2, e1rm_gain=3, coarse_step=10) == 2  # deload-sized, no reset
