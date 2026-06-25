"""Tests for ironlog.engine.ledger.compute_tallies (v0.3 WeeklyLedger).

The 4 pinned tests document the canonical units (load×reps for volume,
distinct sessions for knee counts), the squat-contributes-to-neither
classification check, and the zero-load under-count case. The remaining 9
cover edge cases, default preservation, and multi-session aggregation.
"""
from datetime import datetime, timezone

from ironlog.models.enums import KneeModality, LiftCategory
from ironlog.models.library import Movement
from ironlog.models.session import SetLog
from ironlog.engine.ledger import compute_tallies
from ironlog.engine.validator import WeeklyTallies


# ---------------------------------------------------------------------------
# Factory helpers — module-level functions, shared by every test in the file.
# Mirror the pattern in tests/test_validator.py: plain functions, not fixtures.
# Construct domain objects via constructor kwargs (no DB).
# ---------------------------------------------------------------------------

def make_movement(
    movement_id: int,
    *,
    name: str = "TestMove",
    lift_category: LiftCategory = LiftCategory.NONE,
    knee_modality: KneeModality | None = None,
) -> Movement:
    return Movement(
        id=movement_id,
        name=name,
        base_name=name,
        lift_category=lift_category,
        knee_modality=knee_modality,
    )


def make_setlog(
    *,
    session_id: int,
    movement_id: int,
    set_index: int = 0,
    actual_load: float | None = None,
    actual_reps: int | None = None,
    is_warmup: bool = False,
) -> SetLog:
    return SetLog(
        session_id=session_id,
        movement_id=movement_id,
        set_index=set_index,
        performed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        actual_load=actual_load,
        actual_reps=actual_reps,
        is_warmup=is_warmup,
    )


# ---------------------------------------------------------------------------
# Core (7 cases — including all four pin tests)
# ---------------------------------------------------------------------------

def test_empty_input_returns_default_tallies():
    """Baseline: no set_logs and no movements -> a default-valued WeeklyTallies."""
    result = compute_tallies([], {})
    assert result == WeeklyTallies()
    assert result.knee_counts == {}
    assert result.pull_volume == 0.0
    assert result.push_volume == 0.0
    # defaults from the dataclass — the ledger does NOT produce targets
    assert result.knee_targets == {}
    assert result.pull_push_target == 2.0


def test_pull_volume_is_load_times_reps():
    """PIN: pull volume metric is load * reps (volume-load/tonnage)."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 1000.0
    assert result.push_volume == 0.0


def test_push_volume_is_load_times_reps():
    """PIN: push volume metric is load * reps. Both BENCH and CG_PRESS count."""
    movements = {
        1: make_movement(1, lift_category=LiftCategory.BENCH),
        2: make_movement(2, lift_category=LiftCategory.CG_PRESS),
    }
    logs = [
        make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=10),
        make_setlog(session_id=1, movement_id=2, set_index=1, actual_load=50.0, actual_reps=8),
    ]
    result = compute_tallies(logs, movements)
    assert result.push_volume == 1400.0  # 100*10 + 50*8
    assert result.pull_volume == 0.0


def test_knee_counts_are_distinct_sessions():
    """PIN: knee counts are session-frequency, NOT set-count.

    Three Nordic sets in one session -> count of 1. Two more in a second
    session -> count of 2. This is the standard "Nx/wk" reading.
    """
    movements = {1: make_movement(1, knee_modality=KneeModality.NORDIC)}
    logs = [
        # 3 sets in session 1 -> still count of 1
        make_setlog(session_id=1, movement_id=1, set_index=0, actual_load=0.0, actual_reps=8),
        make_setlog(session_id=1, movement_id=1, set_index=1, actual_load=0.0, actual_reps=8),
        make_setlog(session_id=1, movement_id=1, set_index=2, actual_load=0.0, actual_reps=8),
    ]
    assert compute_tallies(logs, movements).knee_counts == {"NORDIC": 1}

    # Add a 4th in session 2 -> count of 2
    logs.append(make_setlog(session_id=2, movement_id=1, actual_load=0.0, actual_reps=8))
    assert compute_tallies(logs, movements).knee_counts == {"NORDIC": 2}


def test_squat_contributes_to_neither_volume():
    """PIN: a Back Squat set produces zero pull AND zero push volume.

    Movements not in _HORIZONTAL_PULL or _HORIZONTAL_PUSH contribute to
    neither volume. Hip Thrust likewise tested.
    """
    movements = {
        1: make_movement(1, lift_category=LiftCategory.BACK_SQUAT),
        2: make_movement(2, lift_category=LiftCategory.HIP_THRUST),
    }
    logs = [
        make_setlog(session_id=1, movement_id=1, actual_load=225.0, actual_reps=5),
        make_setlog(session_id=1, movement_id=2, set_index=1, actual_load=315.0, actual_reps=8),
    ]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0
    assert result.push_volume == 0.0


def test_zero_load_contributes_zero_volume():
    """PIN: actual_load=0 on a ROW set produces 0 volume (0 * reps == 0).

    The set still qualifies as working per §5.3 (load is non-null, reps
    are non-null, not a warmup), but `0 × 10 == 0`. Documents the
    bodyweight/banded under-count case from spec §5.1.
    """
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=0.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0


def test_zero_load_still_counts_toward_knee_frequency():
    """Companion to the zero-load volume pin: a working set with actual_load=0
    contributes 0 to volume but STILL counts toward its movement's knee
    frequency (§5.1 — "a knee-modality set with actual_load=0 does count
    toward its modality's session-frequency").

    Uses a movement that is BOTH a horizontal pull (lift_category=ROW) AND
    knee-tagged (knee_modality=NORDIC) — no such movement exists in the
    current seed, so this is defensive coverage of the dual-path interaction.
    """
    movements = {
        1: make_movement(1, lift_category=LiftCategory.ROW, knee_modality=KneeModality.NORDIC),
    }
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=0.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0           # 0 × 10 == 0 volume
    assert result.knee_counts == {"NORDIC": 1}  # but still one knee-frequency session


def test_multi_modality_knee_mix():
    """One Nordic, one TIB, two KOT sessions; SISSY unused.

    knee_counts should have exactly three keys (no zero-count entries).
    """
    movements = {
        1: make_movement(1, knee_modality=KneeModality.NORDIC),
        2: make_movement(2, knee_modality=KneeModality.TIB),
        3: make_movement(3, knee_modality=KneeModality.KOT),
        # no SISSY-tagged movement
    }
    logs = [
        make_setlog(session_id=1, movement_id=1, actual_load=0.0, actual_reps=8),
        make_setlog(session_id=2, movement_id=2, actual_load=10.0, actual_reps=12),
        make_setlog(session_id=3, movement_id=3, actual_load=0.0, actual_reps=10),
        make_setlog(session_id=4, movement_id=3, actual_load=0.0, actual_reps=10),
    ]
    result = compute_tallies(logs, movements)
    assert result.knee_counts == {"NORDIC": 1, "TIB": 1, "KOT": 2}
    assert "SISSY" not in result.knee_counts


# ---------------------------------------------------------------------------
# Edge cases (6 cases)
# ---------------------------------------------------------------------------

def test_warmup_skipped():
    """is_warmup=True sets contribute to nothing."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [
        make_setlog(session_id=1, movement_id=1, set_index=0, actual_load=100.0, actual_reps=10, is_warmup=False),
        make_setlog(session_id=1, movement_id=1, set_index=1, actual_load=45.0,  actual_reps=10, is_warmup=True),
    ]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 1000.0  # warmup's 45*10 excluded


def test_null_actual_load_skipped():
    """actual_load=None -> set is skipped (volume can't be computed)."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=None, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0


def test_null_actual_reps_skipped():
    """actual_reps=None -> set is skipped."""
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=None)]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 0.0


def test_missing_movement_silently_skipped():
    """A SetLog referencing a movement_id not in the dict is silently skipped.

    Documents the under-count direction (safer for frequency rules).
    Existing valid SetLog still aggregated correctly.
    """
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW)}
    logs = [
        make_setlog(session_id=1, movement_id=1,  actual_load=100.0, actual_reps=10),  # known
        make_setlog(session_id=1, movement_id=99, actual_load=200.0, actual_reps=10),  # unknown
    ]
    result = compute_tallies(logs, movements)
    # No exception raised; unknown movement contributes nothing; known still counts.
    assert result.pull_volume == 1000.0


def test_targets_left_at_defaults():
    """Ledger does NOT supply knee_targets or pull_push_target — caller's job.

    Returned WeeklyTallies has the dataclass defaults regardless of input.
    """
    movements = {1: make_movement(1, lift_category=LiftCategory.ROW, knee_modality=KneeModality.NORDIC)}
    logs = [make_setlog(session_id=1, movement_id=1, actual_load=100.0, actual_reps=10)]
    result = compute_tallies(logs, movements)
    assert result.knee_targets == {}
    assert result.pull_push_target == 2.0


def test_mixed_multi_session_aggregation():
    """Cross-cutting: 3 sessions with assorted lift categories aggregate correctly.

    Session 1: ROW 100x10 (pull 1000) + BENCH 100x10 (push 1000) + Nordic 0x10 (knee NORDIC count 1)
    Session 2: ROW 110x8 (pull 880)
    Session 3: CG_PRESS 60x6 (push 360) + Lateral Raise 20x12 (NEITHER) + Back Squat 225x5 (NEITHER)
                                                                                  + Nordic 0x10 (knee NORDIC count 2)
    Expected:
      pull_volume  = 1000 + 880 = 1880
      push_volume  = 1000 + 360 = 1360
      knee_counts  = {"NORDIC": 2}
    """
    movements = {
        1: make_movement(1, lift_category=LiftCategory.ROW),
        2: make_movement(2, lift_category=LiftCategory.BENCH),
        3: make_movement(3, knee_modality=KneeModality.NORDIC),
        4: make_movement(4, lift_category=LiftCategory.CG_PRESS),
        5: make_movement(5, lift_category=LiftCategory.NONE),       # Lateral Raise
        6: make_movement(6, lift_category=LiftCategory.BACK_SQUAT),
    }
    logs = [
        # session 1
        make_setlog(session_id=1, movement_id=1, set_index=0, actual_load=100.0, actual_reps=10),
        make_setlog(session_id=1, movement_id=2, set_index=1, actual_load=100.0, actual_reps=10),
        make_setlog(session_id=1, movement_id=3, set_index=2, actual_load=0.0,   actual_reps=10),
        # session 2
        make_setlog(session_id=2, movement_id=1, set_index=0, actual_load=110.0, actual_reps=8),
        # session 3
        make_setlog(session_id=3, movement_id=4, set_index=0, actual_load=60.0,  actual_reps=6),
        make_setlog(session_id=3, movement_id=5, set_index=1, actual_load=20.0,  actual_reps=12),
        make_setlog(session_id=3, movement_id=6, set_index=2, actual_load=225.0, actual_reps=5),
        make_setlog(session_id=3, movement_id=3, set_index=3, actual_load=0.0,   actual_reps=10),
    ]
    result = compute_tallies(logs, movements)
    assert result.pull_volume == 1880.0
    assert result.push_volume == 1360.0
    assert result.knee_counts == {"NORDIC": 2}


def test_engine_package_reexports_ledger_api():
    """compute_tallies and KneeModality are reachable from ironlog.engine
    via identity. Pins the re-export surface (catches future redefinition
    drift the same way Task 7 of v0.2 pinned the validator re-exports)."""
    from ironlog.engine import compute_tallies as eng_compute, KneeModality as eng_km
    from ironlog.engine.ledger import compute_tallies as ledger_compute
    from ironlog.models.enums import KneeModality as models_km
    assert eng_compute is ledger_compute
    assert eng_km is models_km
