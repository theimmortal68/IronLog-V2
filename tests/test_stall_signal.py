"""Tests for engine.stall.build_stall_signal — typed severity taxonomy layered
over the existing detect_stall core (spec §8). See ironlog/engine/stall.py for
the pure detect_stall arms this builder enriches."""
from ironlog.engine.stall import build_stall_signal


def test_failed_progression_low_then_high_severity():
    low = build_stall_signal(1, "d1", consecutive_failed=2, progress_e1rms=[200, 201, 200],
                             current_load=165, limiting_muscle="chest")
    assert low["stall_type"] == "FAILED_PROGRESSION" and low["severity"] == "low"
    high = build_stall_signal(1, "d1", consecutive_failed=5, progress_e1rms=[200, 201, 200],
                              current_load=165, limiting_muscle="chest")
    assert high["severity"] == "high"


def test_plateau_from_flat_e1rm_trend():
    sig = build_stall_signal(1, "d1", consecutive_failed=0, progress_e1rms=[205, 204, 205, 203],
                             current_load=165, limiting_muscle="chest")
    assert sig["stall_type"] == "PLATEAU" and sig["severity"] == "medium"


def test_regression_from_negative_trend():
    sig = build_stall_signal(1, "d1", consecutive_failed=0, progress_e1rms=[210, 205, 198],
                             current_load=165, limiting_muscle="chest")
    assert sig["stall_type"] == "REGRESSION"


def test_no_stall_returns_none():
    assert build_stall_signal(1, "d1", consecutive_failed=0, progress_e1rms=[200, 205, 212],
                              current_load=165, limiting_muscle="chest") is None


def test_signal_has_no_is_swappable_key():
    sig = build_stall_signal(1, "d1", consecutive_failed=2, progress_e1rms=[200, 201, 200],
                             current_load=165, limiting_muscle="chest")
    assert "is_swappable" not in sig and sig["limiting_muscle"] == "chest"
