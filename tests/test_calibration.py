"""Tests for engine.calibration — pure calibration-flip (CALIBRATING -> MEASURED)."""
from ironlog.engine.calibration import evaluate_calibration_flip
from ironlog.models.enums import CalibrationStatus

CALIBRATING = CalibrationStatus.CALIBRATING
INHERITED = CalibrationStatus.INHERITED
MEASURED = CalibrationStatus.MEASURED


def test_flip_when_last_two_within_5pct():
    # 200 vs 205 -> 5/205 = 2.4% <= 5% -> flip
    assert evaluate_calibration_flip([200.0, 205.0], CALIBRATING) is True


def test_no_flip_when_last_two_outside_5pct():
    # 200 vs 215 -> 15/215 = 7% > 5% -> no flip
    assert evaluate_calibration_flip([200.0, 215.0], CALIBRATING) is False


def test_thin_data_zero_estimates_no_flip():
    assert evaluate_calibration_flip([], CALIBRATING) is False


def test_thin_data_one_estimate_no_flip():
    assert evaluate_calibration_flip([200.0], CALIBRATING) is False


def test_one_way_no_flip_from_inherited():
    assert evaluate_calibration_flip([200.0, 201.0], INHERITED) is False


def test_one_way_no_flip_from_measured():
    assert evaluate_calibration_flip([200.0, 201.0], MEASURED) is False


def test_uses_last_two_not_any_two():
    # early pair agrees (200,201) but the LAST two (201, 230) disagree -> no flip
    assert evaluate_calibration_flip([200.0, 201.0, 230.0], CALIBRATING) is False
    # last two agree even though an earlier one is far off -> flip
    assert evaluate_calibration_flip([150.0, 200.0, 204.0], CALIBRATING) is True


def test_boundary_exactly_5pct_flips():
    # 200 vs 210 -> 10/210 = 4.76% <= 5% -> flip; 200 vs 210.6 -> 5.0% boundary
    assert evaluate_calibration_flip([200.0, 210.0], CALIBRATING) is True
