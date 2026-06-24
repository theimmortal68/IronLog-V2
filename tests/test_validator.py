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


def test_primary_after_unknown_movement_is_transparent():
    """Documents PRIMARY_NOT_FIRST's safe-default behavior on missing movements.

    When `ctx.movements.get(ex.movement_id)` returns None, the loop `continue`s
    WITHOUT setting `non_primary_seen = True` — the unknown exercise is
    transparent to the rule. So a primary appearing after an unknown movement is
    NOT rejected. This is internally consistent with the §5 "skip
    movement-dependent checks for that exercise" rule and prevents false
    REJECTs from missing context; it's pinned by this test so future
    maintainers don't accidentally "fix" it into stricter behavior.
    """
    ctx = ValidationContext(movements={
        # movement 1 deliberately absent from ctx.movements (unknown)
        2: make_movement(2, is_primary=True),
    })
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
        make_group(1, GroupType.STRAIGHT, exercises=[make_exercise(2, 0, [make_set(0)])]),
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


# ---------------------------------------------------------------------------
# Task 4 — HT safety pair (HT_BOTTOM_OVER_LIMIT + HT_BAND_NOT_REGISTERED)
# ---------------------------------------------------------------------------

def test_ht_bottom_over_limit_rejects():
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},  # #0 Orange
        ht_bottom_clamp=220.0,
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=220.0, band_pair_id=1)]),
            # 220 + 14 = 234 bottom > 220 clamp
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT]
    assert len(rejects) == 1
    assert rejects[0].set_index == 0
    assert rejects[0].movement_id == 1
    assert "234" in rejects[0].message
    assert "220" in rejects[0].message
    assert "plates+band at bottom position" in rejects[0].message
    assert rejects[0].corrected_value is None


def test_ht_bottom_under_limit_passes():
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=180.0, band_pair_id=1)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT] == []


def test_ht_composite_progression_also_triggers():
    """Lift_category fallback: if not HIP_THRUST, progression_mode==COMPOSITE
    still triggers HT-safety checks."""
    ctx = ValidationContext(
        movements={1: make_movement(1, progression_mode=ProgressionMode.COMPOSITE)},
        band_bottom_lb={1: 14.0},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=220.0, band_pair_id=1)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT]
    assert len(rejects) == 1


def test_ht_unregistered_band_rejects():
    """Fail loud rather than substituting 0 — bottom safety check cannot be
    silently bypassed."""
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},  # only band 1 registered
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=100.0, band_pair_id=99)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BAND_NOT_REGISTERED]
    assert len(rejects) == 1
    assert "99" in rejects[0].message
    assert rejects[0].set_index == 0
    # And the bottom-over-limit check must NOT have run with a silent 0:
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT] == []


def test_ht_incomplete_prescription_skipped():
    """If either target_plates or band_pair_id is None, both HT rules skip."""
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [
                make_set(0, target_plates=220.0, band_pair_id=None),  # missing band
                make_set(1, target_plates=None, band_pair_id=1),       # missing plates
            ]),
        ]),
    ])
    result = validate(session, ctx)
    assert [v for v in result.rejects if v.rule in (RuleCode.HT_BOTTOM_OVER_LIMIT, RuleCode.HT_BAND_NOT_REGISTERED)] == []


# ---------------------------------------------------------------------------
# Task 5 — CLAMP triad
# ---------------------------------------------------------------------------

def test_load_below_floor_clamps():
    ctx = ValidationContext(movements={1: make_movement(1, load_floor=45.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=35.0)]),
        ]),
    ])
    clamps = [v for v in validate(session, ctx).clamps if v.rule == RuleCode.LOAD_BELOW_FLOOR]
    assert len(clamps) == 1
    assert clamps[0].corrected_value == 45.0
    assert clamps[0].set_index == 0
    assert "35" in clamps[0].message and "45" in clamps[0].message


def test_load_at_floor_does_not_clamp():
    ctx = ValidationContext(movements={1: make_movement(1, load_floor=45.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=45.0)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).clamps if v.rule == RuleCode.LOAD_BELOW_FLOOR] == []


def test_load_over_cap_clamps():
    ctx = ValidationContext(movements={1: make_movement(1, cap=25.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=30.0)]),
        ]),
    ])
    clamps = [v for v in validate(session, ctx).clamps if v.rule == RuleCode.LOAD_OVER_CAP]
    assert len(clamps) == 1
    assert clamps[0].corrected_value == 25.0
    assert "30" in clamps[0].message and "25" in clamps[0].message


def test_rpe_over_cap_clamps():
    ctx = ValidationContext(
        movements={1: make_movement(1)},
        phase_hard_cap=8.0,
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_rpe=9.0)]),
        ]),
    ])
    clamps = [v for v in validate(session, ctx).clamps if v.rule == RuleCode.RPE_OVER_CAP]
    assert len(clamps) == 1
    assert clamps[0].corrected_value == 8.0


def test_rpe_cap_exempt_movement_does_not_clamp():
    """HT and similar always-progress movements are exempt from the RPE cap."""
    ctx = ValidationContext(
        movements={1: make_movement(1, rpe_cap_exempt=True)},
        phase_hard_cap=8.0,
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_rpe=9.5)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).clamps if v.rule == RuleCode.RPE_OVER_CAP] == []


def test_no_target_load_skips_load_clamps():
    ctx = ValidationContext(movements={1: make_movement(1, load_floor=45.0, cap=25.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=None)]),
        ]),
    ])
    assert validate(session, ctx).clamps == []


# ---------------------------------------------------------------------------
# Task 6 — cross-session REJECTs (deferred-binding tallies)
# ---------------------------------------------------------------------------

def test_knee_frequency_unmet_rejects():
    ctx = ValidationContext(tallies=WeeklyTallies(
        knee_counts={"NORDIC": 1, "TIB": 1, "KOT": 2, "SISSY": 0},
        knee_targets={"NORDIC": 2, "TIB": 2, "KOT": 2, "SISSY": 1},
    ))
    rejects = [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.KNEE_FREQUENCY]
    # NORDIC owed 1, TIB owed 1, SISSY owed 1 → 3 rejects; KOT is met
    assert len(rejects) == 3
    msgs = " | ".join(v.message for v in rejects)
    assert "NORDIC" in msgs
    assert "TIB" in msgs
    assert "SISSY" in msgs
    assert "KOT" not in msgs


def test_knee_frequency_met_passes():
    ctx = ValidationContext(tallies=WeeklyTallies(
        knee_counts={"NORDIC": 2, "TIB": 2},
        knee_targets={"NORDIC": 2, "TIB": 2},
    ))
    assert [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.KNEE_FREQUENCY] == []


def test_pull_push_ratio_below_target_rejects():
    ctx = ValidationContext(tallies=WeeklyTallies(
        pull_volume=1300.0, push_volume=1000.0, pull_push_target=2.0,
    ))
    rejects = [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.PULL_PUSH_RATIO]
    assert len(rejects) == 1
    assert "1.30" in rejects[0].message
    # `{pull_push_target:.1f}` formats to "2.0"; assert the precise string, not a "2" substring
    assert "2.0" in rejects[0].message


def test_pull_push_ratio_at_target_passes():
    ctx = ValidationContext(tallies=WeeklyTallies(
        pull_volume=2000.0, push_volume=1000.0, pull_push_target=2.0,
    ))
    assert [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.PULL_PUSH_RATIO] == []


def test_pull_push_zero_push_skipped():
    """push_volume == 0 → skip (avoid div-by-zero)."""
    ctx = ValidationContext(tallies=WeeklyTallies(
        pull_volume=500.0, push_volume=0.0, pull_push_target=2.0,
    ))
    assert [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.PULL_PUSH_RATIO] == []


def test_tallies_none_skips_cross_session_entirely():
    """ctx.tallies is None → KNEE_FREQUENCY and PULL_PUSH_RATIO emit nothing
    regardless of session content."""
    ctx = ValidationContext(tallies=None)
    result = validate(make_session(), ctx)
    cross_session = [v for v in result.violations
                     if v.rule in (RuleCode.KNEE_FREQUENCY, RuleCode.PULL_PUSH_RATIO)]
    assert cross_session == []


# ---------------------------------------------------------------------------
# Task 7 — cross-cutting tests
# ---------------------------------------------------------------------------

def test_missing_movement_skips_movement_dependent_rules():
    """Exercise references a movement not in ctx.movements. Movement-dependent
    rules silently skip that exercise; group-level rules still fire."""
    ctx = ValidationContext()  # movements is empty
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=2, exercises=[  # GIANT_SET_ROUNDS will fire
            make_exercise(99, 0, [make_set(0, target_load=10.0)]),  # movement 99 unknown
        ]),
    ])
    result = validate(session, ctx)
    # Movement-dependent rules (PRIMARY_NOT_FIRST, EQUIPMENT_*, HT_*, LOAD_*, RPE_*, SINGLE_KB)
    # should NOT fire — they all key off the missing MovementInfo.
    movement_dependent_rules = {
        RuleCode.PRIMARY_NOT_FIRST, RuleCode.SINGLE_KB,
        RuleCode.EQUIPMENT_NOT_IN_MANIFEST,
        RuleCode.HT_BOTTOM_OVER_LIMIT, RuleCode.HT_BAND_NOT_REGISTERED,
        RuleCode.LOAD_BELOW_FLOOR, RuleCode.LOAD_OVER_CAP, RuleCode.RPE_OVER_CAP,
    }
    for v in result.violations:
        assert v.rule not in movement_dependent_rules, f"unexpected: {v}"
    # GIANT_SET_ROUNDS (group-level, no movement lookup) SHOULD fire:
    assert [v for v in result.rejects if v.rule == RuleCode.GIANT_SET_ROUNDS] != []


def test_apply_loop_round_trip():
    """Build a session triggering LOAD_BELOW_FLOOR + RPE_OVER_CAP on the same
    set, apply both clamps via (rule)-dispatch, re-run validate(), expect
    is_structurally_valid==True and zero clamps."""
    ctx = ValidationContext(
        movements={1: make_movement(1, load_floor=45.0)},
        phase_hard_cap=8.0,
    )
    bad_set = make_set(0, target_load=35.0, target_rpe=9.0)
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [bad_set])]),
    ])
    result = validate(session, ctx)
    assert len(result.clamps) == 2  # below floor + rpe over cap
    assert result.is_structurally_valid is True  # no REJECTs

    # Apply each clamp by (rule) dispatch
    for v in result.clamps:
        target_set = session.groups[v.group_index].exercises[0].planned_sets[v.set_index]
        if v.rule in (RuleCode.LOAD_BELOW_FLOOR, RuleCode.LOAD_OVER_CAP):
            target_set.target_load = v.corrected_value
        elif v.rule == RuleCode.RPE_OVER_CAP:
            target_set.target_rpe = v.corrected_value

    # Re-validate — clean
    result2 = validate(session, ctx)
    assert result2.clamps == []
    assert result2.is_structurally_valid is True


def test_validator_imports_via_engine_package():
    """The public names are re-exported from ironlog.engine (matches the
    pattern for loading, progression, etc.)."""
    from ironlog.engine import (
        validate as eng_validate,
        ValidationContext as eng_ctx,
        ValidationResult as eng_result,
        Violation as eng_violation,
        ViolationKind as eng_kind,
        RuleCode as eng_rule,
        MovementInfo as eng_minfo,
        WeeklyTallies as eng_tallies,
    )
    assert eng_validate is validate
    assert eng_ctx is ValidationContext
    assert eng_result is ValidationResult
    assert eng_violation is Violation
    assert eng_kind is ViolationKind
    assert eng_rule is RuleCode
    assert eng_minfo is MovementInfo
    assert eng_tallies is WeeklyTallies
