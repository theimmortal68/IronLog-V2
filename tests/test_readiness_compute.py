from datetime import date, timedelta

from ironlog.engine.readiness import (
    DailyReadinessInput,
    compute_bw_stable_2wk,
    compute_no_rpe_creep,
    compute_rhr_down,
    compute_sleep_ok,
    compute_strength_bounce,
    compute_subjective_ok,
)


TODAY = date(2026, 7, 18)


def readiness_row(
    days_ago: int,
    *,
    bodyweight=None,
    resting_hr=None,
    sleep_ok=None,
    subjective_ok=None,
) -> DailyReadinessInput:
    return DailyReadinessInput(
        date=TODAY - timedelta(days=days_ago),
        bodyweight=bodyweight,
        resting_hr=resting_hr,
        sleep_ok=sleep_ok,
        subjective_ok=subjective_ok,
    )


def e1rm_history(values):
    start = TODAY - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), value) for i, value in enumerate(values)]


def test_empty_inputs_return_false_for_every_readiness_signal():
    assert compute_bw_stable_2wk([], TODAY) is False
    assert compute_rhr_down([], TODAY, baseline=60.0) is False
    assert (
        compute_rhr_down([readiness_row(0, resting_hr=55.0)], TODAY, baseline=None)
        is False
    )
    assert compute_sleep_ok([], TODAY) is False
    assert compute_subjective_ok([], TODAY) is False
    assert compute_no_rpe_creep([]) is False
    assert compute_strength_bounce([], TODAY) is False


def test_single_day_input_is_insufficient_for_trend_signals():
    rows = [
        readiness_row(
            0,
            bodyweight=200.0,
            resting_hr=55.0,
            sleep_ok=True,
            subjective_ok=True,
        )
    ]

    assert compute_bw_stable_2wk(rows, TODAY) is False
    assert compute_rhr_down(rows, TODAY, baseline=60.0) is False
    assert compute_sleep_ok(rows, TODAY) is False
    assert compute_subjective_ok(rows, TODAY) is False
    assert compute_no_rpe_creep([6.0]) is False
    assert compute_strength_bounce(e1rm_history([100.0]), TODAY) is False


def test_bw_stable_2wk_true_at_tolerance_boundary():
    rows = [readiness_row(days_ago, bodyweight=200.0) for days_ago in range(9)]
    rows.append(readiness_row(9, bodyweight=202.0))
    rows.append(readiness_row(20, bodyweight=250.0))

    assert compute_bw_stable_2wk(rows, TODAY, tolerance=2.0) is True


def test_bw_stable_2wk_false_one_step_past_tolerance():
    rows = [readiness_row(days_ago, bodyweight=200.0) for days_ago in range(9)]
    rows.append(readiness_row(9, bodyweight=202.01))

    assert compute_bw_stable_2wk(rows, TODAY, tolerance=2.0) is False


def test_bw_stable_2wk_false_with_fewer_than_ten_recent_readings():
    rows = [readiness_row(days_ago, bodyweight=200.0) for days_ago in range(9)]

    assert compute_bw_stable_2wk(rows, TODAY) is False


def test_stale_daily_rows_are_insufficient_for_current_readiness():
    rows = [
        readiness_row(
            days_ago,
            bodyweight=200.0,
            resting_hr=55.0,
            sleep_ok=True,
        )
        for days_ago in range(21, 32)
    ]

    assert compute_bw_stable_2wk(rows, TODAY) is False
    assert compute_rhr_down(rows, TODAY, baseline=60.0) is False
    assert compute_sleep_ok(rows, TODAY) is False


def test_rhr_down_true_at_three_bpm_boundary():
    rows = [readiness_row(days_ago, resting_hr=57.0) for days_ago in range(3)]
    rows.append(readiness_row(10, resting_hr=80.0))

    assert compute_rhr_down(rows, TODAY, baseline=60.0) is True


def test_rhr_down_false_just_inside_noise_band():
    rows = [readiness_row(days_ago, resting_hr=57.01) for days_ago in range(3)]

    assert compute_rhr_down(rows, TODAY, baseline=60.0) is False


def test_rhr_down_false_with_fewer_than_three_recent_readings():
    rows = [readiness_row(0, resting_hr=55.0), readiness_row(1, resting_hr=55.0)]

    assert compute_rhr_down(rows, TODAY, baseline=60.0) is False


def test_sleep_ok_true_at_good_ratio_boundary():
    values = [True] * 7 + [False] * 3
    rows = [
        readiness_row(days_ago, sleep_ok=value)
        for days_ago, value in enumerate(values)
    ]

    assert compute_sleep_ok(rows, TODAY, min_good_ratio=0.7) is True


def test_sleep_ok_false_below_good_ratio_boundary():
    values = [True] * 6 + [False] * 4
    rows = [
        readiness_row(days_ago, sleep_ok=value)
        for days_ago, value in enumerate(values)
    ]

    assert compute_sleep_ok(rows, TODAY, min_good_ratio=0.7) is False


def test_sleep_ok_false_with_fewer_than_five_readings():
    rows = [readiness_row(days_ago, sleep_ok=True) for days_ago in range(4)]

    assert compute_sleep_ok(rows, TODAY) is False


def test_subjective_ok_true_at_good_ratio_boundary():
    values = [True] * 7 + [False] * 3
    rows = [
        readiness_row(days_ago, subjective_ok=value)
        for days_ago, value in enumerate(values)
    ]

    assert compute_subjective_ok(rows, TODAY, min_good_ratio=0.7) is True


def test_subjective_ok_false_below_good_ratio_boundary():
    values = [True] * 6 + [False] * 4
    rows = [
        readiness_row(days_ago, subjective_ok=value)
        for days_ago, value in enumerate(values)
    ]

    assert compute_subjective_ok(rows, TODAY, min_good_ratio=0.7) is False


def test_subjective_ok_false_with_fewer_than_five_readings():
    rows = [readiness_row(days_ago, subjective_ok=True) for days_ago in range(4)]

    assert compute_subjective_ok(rows, TODAY) is False


def test_no_rpe_creep_true_at_half_rpe_boundary():
    assert compute_no_rpe_creep([6.0, 6.0, 7.0, 7.0, 6.5, 6.5]) is True


def test_no_rpe_creep_false_one_step_past_half_rpe_boundary():
    assert compute_no_rpe_creep([6.0, 6.0, 7.0, 7.0, 6.51, 6.51]) is False


def test_no_rpe_creep_false_with_fewer_than_three_readings():
    assert compute_no_rpe_creep([6.0, 6.5]) is False


def test_strength_bounce_true_after_prior_decline():
    assert compute_strength_bounce(
        e1rm_history([100.0, 99.0, 98.0, 99.0, 101.0, 103.0]),
        TODAY,
    ) is True


def test_strength_bounce_false_for_lift_climbing_the_whole_time():
    assert compute_strength_bounce(
        e1rm_history([100.0, 102.0, 104.0, 106.0]),
        TODAY,
    ) is False


def test_strength_bounce_true_at_one_percent_bounce_boundary():
    assert compute_strength_bounce(
        e1rm_history([100.0, 100.0, 100.0, 101.0]),
        TODAY,
    ) is True


def test_strength_bounce_false_below_one_percent_bounce_boundary():
    assert compute_strength_bounce(
        e1rm_history([100.0, 100.0, 100.0, 100.99]),
        TODAY,
    ) is False


def test_strength_bounce_false_with_fewer_than_four_readings():
    assert (
        compute_strength_bounce(e1rm_history([100.0, 99.0, 101.0]), TODAY)
        is False
    )


def test_strength_bounce_false_when_history_is_stale():
    history = [
        (TODAY - timedelta(days=days_ago), value)
        for days_ago, value in zip(
            range(80, 74, -1),
            [100.0, 99.0, 98.0, 99.0, 101.0, 103.0],
        )
    ]

    assert compute_strength_bounce(history, TODAY) is False
