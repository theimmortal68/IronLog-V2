from datetime import date

from ironlog.models.enums import GroupType, LiftCategory, ProgressionMode, Scheme, Objective, SetRole
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session,
)
from ironlog.engine.validator import (
    MovementInfo, RuleCode, ValidationContext, ValidationResult, Violation,
    ViolationKind, WeeklyTallies, validate,
)


# ---------------------------------------------------------------------------
# Helpers — module-level factory functions, shared by every test in the file.
# Mirror the pattern in tests/test_loading.py: plain functions, not fixtures.
# Construct domain objects via constructor kwargs (no DB).
# ---------------------------------------------------------------------------

def make_set(set_index: int = 0, *, target_load=None, target_rpe=None,
             target_plates=None, band_pair_id=None,
             set_role: SetRole = SetRole.WORKING) -> PlannedSet:
    return PlannedSet(
        planned_exercise_id=0, set_index=set_index, set_role=set_role,
        target_load=target_load, target_rpe=target_rpe,
        target_plates=target_plates, band_pair_id=band_pair_id,
    )


def make_exercise(movement_id: int, order_index: int = 0,
                  sets=None, *,
                  scheme: Scheme = Scheme.STRAIGHT,
                  objective: Objective = Objective.MAINTAIN) -> PlannedExercise:
    return PlannedExercise(
        group_id=0, movement_id=movement_id, order_index=order_index,
        scheme=scheme, objective=objective,
        planned_sets=list(sets or []),
    )


def make_group(order_index: int, group_type: GroupType, *,
               rounds: int = 1, exercises=None) -> ExerciseGroup:
    return ExerciseGroup(
        session_id=0, order_index=order_index, group_type=group_type,
        rounds=rounds, exercises=list(exercises or []),
    )


def make_session(groups=None) -> Session:
    return Session(
        date=date(2026, 1, 1), day_role="Upper A", phase="CUT",
        groups=list(groups or []),
    )


def make_movement(movement_id: int, *,
                  is_primary: bool = False,
                  load_equipment_id=None,
                  load_floor=None,
                  cap=None,
                  rpe_cap_exempt: bool = False,
                  lift_category: LiftCategory = LiftCategory.NONE,
                  progression_mode: ProgressionMode = ProgressionMode.NONE) -> MovementInfo:
    return MovementInfo(
        movement_id=movement_id,
        is_primary=is_primary,
        load_equipment_id=load_equipment_id,
        load_floor=load_floor,
        cap=cap,
        rpe_cap_exempt=rpe_cap_exempt,
        lift_category=lift_category,
        progression_mode=progression_mode,
    )


# ---------------------------------------------------------------------------
# Task 1 — Contract scaffolding tests
# ---------------------------------------------------------------------------

def test_empty_session_is_structurally_valid():
    result = validate(make_session(), ValidationContext())
    assert result.violations == []
    assert result.is_structurally_valid is True
    assert result.rejects == []
    assert result.clamps == []


def test_rejects_property_filters_to_rejects():
    result = ValidationResult(violations=[
        Violation(kind=ViolationKind.REJECT, rule=RuleCode.GIANT_SET_ROUNDS, message="r"),
        Violation(kind=ViolationKind.CLAMP,  rule=RuleCode.LOAD_OVER_CAP,    message="c", corrected_value=10.0),
    ])
    assert len(result.rejects) == 1
    assert result.rejects[0].rule == RuleCode.GIANT_SET_ROUNDS
    assert len(result.clamps) == 1
    assert result.clamps[0].rule == RuleCode.LOAD_OVER_CAP


def test_is_structurally_valid_false_with_reject():
    result = ValidationResult(violations=[
        Violation(kind=ViolationKind.REJECT, rule=RuleCode.GIANT_SET_ROUNDS, message="r"),
    ])
    assert result.is_structurally_valid is False


def test_is_structurally_valid_true_with_only_clamps():
    """Clamps do NOT affect is_structurally_valid. The caller decides whether
    clamps are acceptable for its context."""
    result = ValidationResult(violations=[
        Violation(kind=ViolationKind.CLAMP, rule=RuleCode.LOAD_OVER_CAP, message="c", corrected_value=25.0),
        Violation(kind=ViolationKind.CLAMP, rule=RuleCode.RPE_OVER_CAP,  message="c", corrected_value=8.0),
    ])
    assert result.is_structurally_valid is True
    assert len(result.clamps) == 2


def test_rulecode_enum_has_all_12_members():
    expected = {
        # Structural REJECT
        "PRIMARY_NOT_FIRST", "GIANT_SET_ROUNDS", "GIANT_SET_CONCURRENCY",
        "SINGLE_KB", "EQUIPMENT_NOT_IN_MANIFEST",
        "HT_BOTTOM_OVER_LIMIT", "HT_BAND_NOT_REGISTERED",
        # Numeric CLAMP
        "LOAD_BELOW_FLOOR", "LOAD_OVER_CAP", "RPE_OVER_CAP",
        # Cross-session REJECT
        "KNEE_FREQUENCY", "PULL_PUSH_RATIO",
    }
    actual = {r.name for r in RuleCode}
    assert actual == expected, f"missing: {expected - actual}, extra: {actual - expected}"
    # CONDITIONING_PLACEMENT is explicitly deferred per spec §10 — must not appear.
    assert "CONDITIONING_PLACEMENT" not in actual
