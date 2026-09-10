"""Golden regression baseline for legacy APEX Bridge generation behavior.

Expected values were captured from the seed before the program-library migration.
They intentionally remain literal so later execution changes cannot move the baseline.
"""
from datetime import date, datetime, timedelta

from sqlmodel import select

from ironlog.engine.program_hash import (
    compute_program_prescription_hash,
    compute_slot_topology_hash,
)
from ironlog.engine.stall import detect_stall
from ironlog.engine.validator import validate
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context, should_invoke_llm
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session, generate_session, is_clean
from ironlog.generation.proposer import StubProposer
from ironlog.generation.repair import build_validation_context
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import Objective
from ironlog.models.library import DailyReadiness, EngineState, Movement, MovementState
from ironlog.models.periodization import (
    BodyCompState, BodyCompStateValue, Macrocycle, Mesocycle, MesocycleTemplate,
    Microcycle, MicrocycleLifecycleStatus, MicrocycleSlot,
    MicrocycleSlotType, PlanStatus, RecoveryStatus, RecoveryStatusValue,
)
from ironlog.models.program import MissedDayRecord, Program, ProgramDay
from ironlog.persistence.missed_days import check_missed_days


FIXED_AS_OF = date(2026, 9, 10)
PRESCRIPTION_HASH = "4e5256b6972761cf765de22ef914a65f99a71e49693252496154403a3ac0a269"


def _week_key(value):
    return value.year, value.isocalendar()[1]


EXPECTED_TOPOLOGY = {
    "D1 Upper Push": ((63, 4), (
        ("d1_t2f", 57, 3, "free", "giant", "T2 GS"),
        ("d1_t2h", 146, 3, "free", "giant", "T2 GS"),
        ("d1_t2g", 142, 3, "free", "giant", "T2 GS"),
        ("d1_t3a", 31, 4, "free", "giant", "T3 GS"),
        ("d1_t3d", 100, 4, "free", "giant", "T3 GS"),
        ("d1_t3f", 75, 4, "free", "giant", "T3 GS"),
    )),
    "D2 Lower A": ((3,), (
        ("d2_t2d", 49, 2, "free", "knee", "T2 GS"),
        ("d2_t2e", 18, 2, "free", "knee", "T2 GS"),
        ("d2_t3d", 50, 2, "free", "giant", "T2 GS"),
        ("d2_t3a", 38, 3, "free", "knee", "T3 GS"),
        ("d2_t3e", 51, 3, "free", "knee", "T3 GS"),
        ("d2_t2f", 98, 3, "free", "giant", "T3 GS"),
    )),
    "D4 Upper Pull": ((59, 58), (
        ("d4_t2d", 60, 3, "free", "giant", "T2 GS"),
        ("d4_t3d", 97, 3, "free", "giant", "T2 GS"),
        ("d4_t2g", 55, 3, "free", "giant", "T2 GS"),
        ("d4_t3h", 134, 4, "free", "giant", "T3 GS"),
        ("d4_t2e", 99, 4, "free", "giant", "T3 GS"),
        ("d4_t3g", 61, 4, "free", "giant", "T3 GS"),
    )),
    "D5 Lower B": ((22,), (
        ("d5_t2i", 20, 2, "free", "giant", "GS1"),
        ("d5_t4a", 29, 2, "free", "giant", "GS1"),
        ("d5_t3f", 27, 2, "free", "knee", "GS1"),
        ("d5_t3g", 28, 2, "free", "giant", "GS1"),
        ("d5_t2h", 24, 3, "free", "giant", "GS2"),
        ("d5_t3b", 17, 3, "free", "knee", "GS2"),
        ("d5_t3e", 26, 3, "free", "giant", "GS2"),
        ("d5_t2f", 25, 3, "free", "giant", "GS2"),
    )),
    "D6 Weak Points": ((5,), (
        ("d6_g1a", 33, 2, "anchor", "accessory", "GS1"),
        ("d6_g1h", 47, 2, "free", "giant", "GS1"),
        ("d6_g2i", 135, 2, "free", "giant", "GS1"),
        ("d6_g2e", 132, 3, "free", "giant", "GS2"),
        ("d6_g2h", 130, 3, "free", "giant", "GS2"),
        ("d6_g1e", 129, 3, "free", "giant", "GS2"),
        ("d6_g3a", 83, 4, "free", "giant", "GS3"),
        ("d6_g3d", 136, 4, "free", "giant", "GS3"),
        ("d6_g3e", 137, 4, "free", "giant", "GS3"),
    )),
}


def _topology(skeleton):
    return tuple(skeleton.anchor_movement_ids), tuple(
        (slot.slot_id, slot.program_movement_id, slot.tier_order,
         slot.tier_role, slot.kind, slot.group_key)
        for slot in skeleton.adaptive_slots
    )


def _planned(session):
    """Compact exact projection: each exercise includes every planned set."""
    return tuple(
        (group.order_index, group.group_type.value, group.label, tuple(
            (exercise.movement_id, len(exercise.planned_sets), tuple(
                (planned.set_index, planned.set_role.value,
                 planned.target_reps_low, planned.target_reps_high,
                 planned.target_load, planned.target_rpe)
                for planned in sorted(
                    exercise.planned_sets, key=lambda row: row.set_index
                )
            ))
            for exercise in sorted(group.exercises, key=lambda row: row.order_index)
        ))
        for group in sorted(session.groups, key=lambda row: row.order_index)
    )


EXPECTED_D1 = (
    (0, "ALT_PAIR", "T1b/T1", (
        (63, 3, ((0, "WORKING", 4, 6, 100.0, 7.5),
                 (1, "WORKING", 4, 6, 100.0, 7.5),
                 (2, "WORKING", 4, 6, 100.0, 7.5))),
        (4, 6, ((-3, "RAMP", 5, 5, 45.0, None),
                (-2, "RAMP", 3, 3, 60, None),
                (-1, "RAMP", 2, 2, 80, None),
                (0, "WORKING", 4, 6, 100.0, 8.0),
                (1, "WORKING", 4, 6, 100.0, 8.0),
                (2, "WORKING", 4, 6, 100.0, 8.0))),
    )),
    (1, "GIANT_SET", "T2 GS", (
        (57, 3, ((0, "WORKING", 8, 12, 100.0, 7.5),
                 (1, "WORKING", 8, 12, 100.0, 7.5),
                 (2, "WORKING", 8, 12, 100.0, 7.5))),
        (146, 3, ((0, "WORKING", 8, 12, 100.0, 7.5),
                  (1, "WORKING", 8, 12, 100.0, 7.5),
                  (2, "WORKING", 8, 12, 100.0, 7.5))),
        (142, 3, ((0, "WORKING", 8, 12, 100.0, 7.5),
                  (1, "WORKING", 8, 12, 100.0, 7.5),
                  (2, "WORKING", 8, 12, 100.0, 7.5))),
    )),
    (2, "GIANT_SET", "T3 GS", (
        (31, 3, ((0, "WORKING", 4, 6, 0.0, 7.5),
                 (1, "WORKING", 4, 6, 0.0, 7.5),
                 (2, "WORKING", 4, 6, 0.0, 7.5))),
        (100, 3, ((0, "WORKING", 8, 12, None, 7.5),
                  (1, "WORKING", 8, 12, None, 7.5),
                  (2, "WORKING", 8, 12, None, 7.5))),
        (75, 3, ((0, "WORKING", 10, 15, 100.0, 7.5),
                 (1, "WORKING", 10, 15, 100.0, 7.5),
                 (2, "WORKING", 10, 15, 100.0, 7.5))),
    )),
)


def _assemble_d1(db):
    skeleton = lay_skeleton("D1 Upper Push", db, as_of=FIXED_AS_OF)
    context = resolve_context("D1 Upper Push", skeleton, db, _week_key)
    return skeleton, context, assemble(
        program_selections(skeleton), skeleton, context, db
    )


def test_resolved_topology_for_every_real_program_day(gen_db):
    days = gen_db.exec(
        select(ProgramDay).where(ProgramDay.is_rest == False)  # noqa: E712
        .order_by(ProgramDay.day_index)
    ).all()
    actual = {
        row.day_role: _topology(
            lay_skeleton(row.day_role, gen_db, as_of=FIXED_AS_OF)
        )
        for row in days
    }

    assert tuple(actual) == (
        "D1 Upper Push", "D2 Lower A", "D4 Upper Pull",
        "D5 Lower B", "D6 Weak Points",
    )
    assert actual == EXPECTED_TOPOLOGY


def test_seeded_program_hashes(gen_db):
    program = gen_db.exec(select(Program)).one()
    assert compute_program_prescription_hash(program) == PRESCRIPTION_HASH
    assert compute_slot_topology_hash(program) == (
        "b8bcfb8912e37f763adc74f78a1877d5a2db8c8647e24f4c5ceba8b80418c4b6"
    )


def test_candidate_menu_and_program_selections_are_golden(gen_db):
    skeleton = lay_skeleton("D1 Upper Push", gen_db, as_of=FIXED_AS_OF)
    context = resolve_context("D1 Upper Push", skeleton, gen_db, _week_key)
    selections = program_selections(skeleton)

    assert context.candidate_menus["d1_t2f"] == [57, 52, 53, 54]
    assert (selections.ordering,
            [(row.slot_id, row.movement_id) for row in selections.slots],
            selections.rationale) == (
        ["d1_t2f", "d1_t2h", "d1_t2g", "d1_t3a", "d1_t3d", "d1_t3f"],
        [("d1_t2f", 57), ("d1_t2h", 146), ("d1_t2g", 142),
         ("d1_t3a", 31), ("d1_t3d", 100), ("d1_t3f", 75)],
        "deterministic program emission (the prior)",
    )


def test_llm_gate_is_false_for_quiet_week(gen_db):
    skeleton = lay_skeleton("D1 Upper Push", gen_db, as_of=FIXED_AS_OF)
    context = resolve_context("D1 Upper Push", skeleton, gen_db, _week_key)
    assert should_invoke_llm(skeleton, context) is False


def test_stall_signal_and_llm_gate_are_golden(stalled_session_db):
    movement = stalled_session_db.exec(select(Movement).where(
        Movement.name == "Better Fly Sagittal Lat Pulldown [FT]"
    )).one()
    state = stalled_session_db.exec(select(MovementState).where(
        MovementState.movement_id == movement.id
    )).one()
    signal = detect_stall([], state.consecutive_failed_progressions, Objective.PROGRESS)
    skeleton = lay_skeleton("D1 Upper Push", stalled_session_db, as_of=FIXED_AS_OF)
    context = resolve_context("D1 Upper Push", skeleton, stalled_session_db, _week_key)

    assert (movement.id, state.consecutive_failed_progressions,
            signal.trend_stalled, signal.failed_stalled, signal.stalled,
            context.weak_point_hints[movement.id],
            should_invoke_llm(skeleton, context)) == (
        146, 2, False, True, True,
        {"stall_type": "failed", "failed_count": 2,
         "e1rm_window": {"sessions": 0, "peak": None, "latest": None},
         "limiter": {"primary_muscle": "LATS",
                     "secondary_muscles": ["BICEPS", "MID_BACK"]}},
        True,
    )


def test_validator_and_full_d1_prescription_are_golden(gen_db_calibrated):
    skeleton, context, assembled = _assemble_d1(gen_db_calibrated)
    result = validate(
        assembled.session, build_validation_context(context, gen_db_calibrated)
    )
    outcome = generate_session(
        "D1 Upper Push",
        gen_db_calibrated,
        StubProposer(program_selections(skeleton)),
        _week_key,
    )

    assert (result.is_structurally_valid, len(result.rejects), len(result.clamps)) == (
        True, 0, 0,
    )
    assert (outcome.attempts, outcome.clamps_applied, outcome.rejections,
            outcome.exhausted, is_clean(outcome)) == (0, 0, [], False, True)
    assert _planned(assembled.session) == EXPECTED_D1


def test_commit_applies_literal_progression_result(gen_db_calibrated):
    state = gen_db_calibrated.exec(select(MovementState).where(
        MovementState.movement_id == 63
    )).one()
    assert (state.current_load, state.current_increment_tier,
            state.pending_load_delta) == (100.0, 0, None)
    state.pending_load_delta = 2.5
    gen_db_calibrated.add(state)
    gen_db_calibrated.commit()

    _, _, assembled = _assemble_d1(gen_db_calibrated)
    assert assembled.prospective_current_loads[63] == 102.5
    commit_session(
        assembled, gen_db_calibrated, approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )
    saved = gen_db_calibrated.exec(select(MovementState).where(
        MovementState.movement_id == 63,
        MovementState.day_id == "D1 Upper Push",
    )).one()
    assert (saved.current_load, saved.current_increment_tier,
            saved.pending_load_delta) == (102.5, 0, None)


def _seed_poor_readiness_microcycle(db):
    today = date.today()
    program = db.exec(select(Program)).one()
    macro = Macrocycle(
        goal="golden readiness baseline",
        planned_start_date=today - timedelta(days=14),
        planned_end_date=today + timedelta(days=70), status=PlanStatus.ACTIVE,
    )
    template = MesocycleTemplate(
        name="golden readiness baseline", postures=["PUSH"]
    )
    db.add_all([macro, template])
    db.flush()
    mesocycle = Mesocycle(
        template_id=template.id, macrocycle_id=macro.id, program_id=program.id,
        ordinal=1, planned_start_date=today - timedelta(days=7),
        planned_end_date=today + timedelta(days=21),
        program_prescription_hash=PRESCRIPTION_HASH, status=PlanStatus.ACTIVE,
    )
    db.add(mesocycle)
    db.flush()
    microcycle = Microcycle(
        mesocycle_id=mesocycle.id, ordinal=1,
        planned_start_date=today - timedelta(days=2),
        planned_end_date=today + timedelta(days=4), expected_sessions=5,
        lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
        planned_posture="PUSH",
    )
    db.add(microcycle)
    db.flush()
    db.add_all([
        DailyReadiness(date=today, sleep_ok=False, subjective_ok=False),
        BodyCompState(state=BodyCompStateValue.CUT,
                      effective_from=today - timedelta(days=30)),
        RecoveryStatus(
            as_of_date=today, status=RecoveryStatusValue.POOR,
            inputs_snapshot={"sleep_ok": False, "subjective_ok": False},
        ),
        MicrocycleSlot(
            microcycle_id=microcycle.id, ordinal=1, day_code="D1",
            day_label="D1 Upper Push", planned_date=today,
            slot_type=MicrocycleSlotType.TRAINING,
        ),
    ])
    db.commit()


def test_poor_readiness_modifies_envelope_and_prescription(gen_db_calibrated):
    _seed_poor_readiness_microcycle(gen_db_calibrated)
    _, context, assembled = _assemble_d1(gen_db_calibrated)
    first = sorted(
        sorted(assembled.session.groups, key=lambda row: row.order_index)[0].exercises,
        key=lambda row: row.order_index,
    )[0]

    assert (context.current_recovery_status.status.value,
            context.current_recovery_status.inputs_snapshot,
            context.resolved_envelope.rpe_cap,
            context.resolved_envelope.volume_multiplier,
            context.resolved_envelope.progression_mode,
            context.resolved_envelope.optional_work_eligible) == (
        "POOR", {"sleep_ok": False, "subjective_ok": False},
        7.0, 0.68, "SUPPRESSED", False,
    )
    assert (first.movement_id, first.objective.value, len(first.planned_sets),
            [row.target_rpe for row in first.planned_sets]) == (
        63, "MAINTAIN", 2, [7.0, 7.0],
    )


def test_missed_day_record_creation_is_golden(gen_db):
    engine_state = gen_db.get(EngineState, 1)
    engine_state.active_program_id = 1
    gen_db.add(engine_state)
    gen_db.commit()
    summary = check_missed_days(gen_db, as_of=datetime(2026, 7, 14, 6, 0, 0))
    records = gen_db.exec(select(MissedDayRecord)).all()

    assert summary == {"newly_missed": 1, "resolved": 0}
    assert [(row.program_day_id, row.week_start_date, row.status, row.resolved_at)
            for row in records] == [(1, date(2026, 7, 13), "PENDING", None)]


def test_committed_session_retains_microcycle_identity(gen_db_calibrated):
    _seed_poor_readiness_microcycle(gen_db_calibrated)
    _, _, assembled = _assemble_d1(gen_db_calibrated)
    assert assembled.session.prescription_snapshot == {
        "macrocycle_id": 1, "mesocycle_id": 1, "microcycle_id": 1,
        "planned_posture": "PUSH", "body_comp_state": "CUT",
        "recovery_status": "POOR",
        "deload_state": {"active": False, "trigger_reason": None},
        "resolved_envelope": {
            "rpe_cap": 7.0, "volume_multiplier": 0.68,
            "progression_mode": "SUPPRESSED",
            "optional_work_eligible": False,
        },
        "resolver_policy_version": "periodization_resolver.v1",
    }
    saved = commit_session(
        assembled, gen_db_calibrated, approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )
    slot = gen_db_calibrated.exec(select(MicrocycleSlot)).one()

    assert (saved.id, saved.microcycle_id, saved.plan_status.value) == (1, 1, "PLANNED")
    assert (slot.session_id, slot.resolution.value) == (1, "PENDING")
