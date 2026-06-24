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


# ---------------------------------------------------------------------------
# Task 2 — PRIMARY_NOT_FIRST
# ---------------------------------------------------------------------------

def test_primary_in_giant_set_rejects():
    ctx = ValidationContext(movements={
        1: make_movement(1, is_primary=True),
        2: make_movement(2, is_primary=False),
    })
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    result = validate(session, ctx)
    rejects = [v for v in result.rejects if v.rule == RuleCode.PRIMARY_NOT_FIRST]
    assert len(rejects) == 1
    assert rejects[0].movement_id == 1
    assert "GIANT_SET" in rejects[0].message
    assert rejects[0].corrected_value is None


def test_primary_after_non_primary_rejects():
    ctx = ValidationContext(movements={
        1: make_movement(1, is_primary=False),
        2: make_movement(2, is_primary=True),
    })
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
        make_group(1, GroupType.STRAIGHT, exercises=[make_exercise(2, 0, [make_set(0)])]),
    ])
    result = validate(session, ctx)
    rejects = [v for v in result.rejects if v.rule == RuleCode.PRIMARY_NOT_FIRST]
    assert len(rejects) == 1
    assert rejects[0].movement_id == 2
    assert "after non-primary" in rejects[0].message


def test_primary_first_then_accessory_passes():
    ctx = ValidationContext(movements={
        1: make_movement(1, is_primary=True),
        2: make_movement(2, is_primary=False),
        3: make_movement(3, is_primary=False),
    })
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
        make_group(1, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(2, 0, [make_set(0)]),
            make_exercise(3, 1, [make_set(0)]),
        ]),
    ])
    result = validate(session, ctx)
    assert [v for v in result.rejects if v.rule == RuleCode.PRIMARY_NOT_FIRST] == []


# ---------------------------------------------------------------------------
# Task 3 — group-shape & manifest REJECTs
# ---------------------------------------------------------------------------

def test_giant_set_rounds_must_be_3():
    ctx = ValidationContext(movements={
        1: make_movement(1),
        2: make_movement(2),
    })
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=2, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_ROUNDS]
    assert len(rejects) == 1
    assert "rounds=2" in rejects[0].message
    assert rejects[0].group_index == 0


def test_giant_set_rounds_3_passes():
    ctx = ValidationContext(movements={1: make_movement(1)})
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[make_exercise(1, 0, [make_set(0)])]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_ROUNDS] == []


def test_giant_set_concurrency_rejects_4_exercises():
    ctx = ValidationContext(movements={i: make_movement(i) for i in range(1, 5)})
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(i, idx, [make_set(0)]) for idx, i in enumerate(range(1, 5))
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_CONCURRENCY]
    assert len(rejects) == 1
    assert "4 exercises" in rejects[0].message


def test_giant_set_concurrency_rejects_empty():
    ctx = ValidationContext()
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_CONCURRENCY]
    assert len(rejects) == 1


def test_giant_set_concurrency_1_to_3_passes():
    ctx = ValidationContext(movements={i: make_movement(i) for i in range(1, 4)})
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(i, idx, [make_set(0)]) for idx, i in enumerate(range(1, 4))
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_CONCURRENCY] == []


def test_single_kb_rejects_2_kettlebells_in_giant_set():
    KB_EQUIP_ID = 12
    ctx = ValidationContext(
        kettlebell_equipment_id=KB_EQUIP_ID,
        movements={
            1: make_movement(1, load_equipment_id=KB_EQUIP_ID),
            2: make_movement(2, load_equipment_id=KB_EQUIP_ID),
        },
    )
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.SINGLE_KB]
    assert len(rejects) == 1
    assert "2 kettlebell" in rejects[0].message
    assert rejects[0].group_index == 0


def test_single_kb_one_kb_in_giant_set_passes():
    KB_EQUIP_ID = 12
    ctx = ValidationContext(
        kettlebell_equipment_id=KB_EQUIP_ID,
        movements={
            1: make_movement(1, load_equipment_id=KB_EQUIP_ID),
            2: make_movement(2, load_equipment_id=1),  # not KB
        },
    )
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.SINGLE_KB] == []


def test_single_kb_skipped_when_no_kb_equipment_id():
    """If ctx.kettlebell_equipment_id is None, the rule is a no-op even with
    multiple exercises sharing the same load_equipment_id."""
    ctx = ValidationContext(
        kettlebell_equipment_id=None,
        movements={
            1: make_movement(1, load_equipment_id=12),
            2: make_movement(2, load_equipment_id=12),
        },
    )
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.SINGLE_KB] == []


def test_equipment_not_in_manifest_rejects():
    ctx = ValidationContext(
        movements={1: make_movement(1, load_equipment_id=99)},
        manifest_equipment_ids={1, 2, 3},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.EQUIPMENT_NOT_IN_MANIFEST]
    assert len(rejects) == 1
    assert rejects[0].movement_id == 1
    assert "99" in rejects[0].message


def test_equipment_in_manifest_passes():
    ctx = ValidationContext(
        movements={1: make_movement(1, load_equipment_id=2)},
        manifest_equipment_ids={1, 2, 3},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.EQUIPMENT_NOT_IN_MANIFEST] == []
