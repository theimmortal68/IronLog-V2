"""Task 3 — HT bottom-clamp validator sums a multi-band `band_config`.

Mirrors the fixture-construction pattern in tests/test_validator.py (plain
constructor kwargs, no DB) but exercises `_check_ht_safety` directly per the
task-3 brief, with a local `ht_ctx` fixture and `_ht_session` helper.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date

import pytest

from ironlog.models.enums import GroupType, LiftCategory, Scheme, Objective, SetRole
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session
from ironlog.engine.validator import MovementInfo, RuleCode, ValidationContext, _check_ht_safety


@pytest.fixture
def ht_ctx() -> ValidationContext:
    return ValidationContext(
        movements={1: MovementInfo(movement_id=1, is_primary=False,
                                    lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={0: 18.0, 1: 36.0},  # #0 Orange, #1 Red
        ht_bottom_clamp=220.0,
    )


def _ht_session(*, target_plates, band_config) -> Session:
    ps = PlannedSet(
        planned_exercise_id=0, set_index=0, set_role=SetRole.WORKING,
        target_plates=target_plates, band_config=band_config,
    )
    ex = PlannedExercise(
        group_id=0, movement_id=1, order_index=0,
        scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN,
        planned_sets=[ps],
    )
    group = ExerciseGroup(
        session_id=0, order_index=0, group_type=GroupType.STRAIGHT,
        rounds=1, exercises=[ex],
    )
    return Session(date=date(2026, 1, 1), day_role="Upper A", phase="CUT", groups=[group])


def test_two_band_config_over_clamp_rejected(ht_ctx):
    # plates 200 + Orange(18)+Red(36) = 254 bottom > 220 -> HT_BOTTOM_OVER_LIMIT
    session = _ht_session(target_plates=200, band_config=[0, 1])
    v = _check_ht_safety(session, ht_ctx)
    assert any(x.rule == RuleCode.HT_BOTTOM_OVER_LIMIT for x in v)


def test_legal_config_passes(ht_ctx):
    session = _ht_session(target_plates=150, band_config=[0, 1])  # 150+54=204 <= 220
    assert _check_ht_safety(session, ht_ctx) == []


def test_unregistered_band_in_config_rejected(ht_ctx):
    session = _ht_session(target_plates=100, band_config=[0, 99])  # 99 not registered
    v = _check_ht_safety(session, ht_ctx)
    assert any(x.rule == RuleCode.HT_BAND_NOT_REGISTERED for x in v)
