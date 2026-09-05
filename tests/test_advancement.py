"""Regression tests for deterministic training-state advancement.

NO from __future__ import annotations (project-wide constraint).
"""
import logging
from datetime import date, timedelta

import pytest
from sqlmodel import SQLModel, Session as DbSession, create_engine, select

import ironlog.engine.advancement as advancement
import ironlog.models  # noqa: F401 - register all tables
from ironlog.engine.advancement import (
    AWAITING_NEXT_MESOCYCLE,
    PROGRAM_DRIFT,
    WAITING_FOR_MICROCYCLE_START,
    InvalidPlanConfigurationError,
    ensure_first_microcycle_instantiated,
    mark_microcycle_incomplete,
    reconcile_current_training_state,
)
from ironlog.engine.program_hash import (
    compute_program_prescription_hash,
    compute_slot_topology_hash,
)
from ironlog.models.periodization import (
    AdvancementLog,
    MacroPlanningState,
    Macrocycle,
    Mesocycle,
    MesocycleTemplate,
    Microcycle,
    MicrocycleLifecycleStatus,
    MicrocycleSlot,
    MicrocycleSlotResolution,
    MicrocycleSlotResolutionSource,
    MicrocycleSlotType,
    PlanStatus,
)
from ironlog.models.program import Program, ProgramDay


BASE_DATE = date(2026, 9, 7)
_COMPUTE_HASH = object()


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_program(db, rest_flags=(False, True, False)):
    program = Program(name="Advancement Test", phase="TEST", duration_weeks=4)
    db.add(program)
    db.flush()

    for day_index, is_rest in enumerate(rest_flags, start=1):
        db.add(
            ProgramDay(
                program_id=program.id,
                day_index=day_index,
                day_role="" if is_rest else f"D{day_index} Training",
                is_rest=is_rest,
            )
        )
    db.flush()
    return program


def _seed_template(db, postures=("BUILD",), name="advancement template"):
    template = MesocycleTemplate(name=name, postures=list(postures))
    db.add(template)
    db.flush()
    return template


def _seed_macrocycle(
    db,
    start=BASE_DATE,
    planning_state=MacroPlanningState.ACTIVE,
):
    macrocycle = Macrocycle(
        goal="advancement test",
        planned_start_date=start,
        planned_end_date=start + timedelta(days=83),
        status=PlanStatus.ACTIVE,
        planning_state=planning_state,
    )
    db.add(macrocycle)
    db.flush()
    return macrocycle


def _seed_mesocycle(
    db,
    template,
    *,
    program=None,
    macrocycle=None,
    ordinal=1,
    start=BASE_DATE,
    status=PlanStatus.PLANNED,
    program_hash=_COMPUTE_HASH,
):
    if program_hash is _COMPUTE_HASH:
        program_hash = (
            compute_program_prescription_hash(program)
            if program is not None
            else None
        )
    mesocycle = Mesocycle(
        template_id=template.id,
        macrocycle_id=macrocycle.id if macrocycle is not None else None,
        program_id=program.id if program is not None else None,
        ordinal=ordinal,
        planned_start_date=start,
        planned_end_date=start + timedelta(days=7 * len(template.postures) - 1),
        program_prescription_hash=program_hash,
        status=status,
    )
    db.add(mesocycle)
    db.flush()
    return mesocycle


def _seed_microcycle(
    db,
    mesocycle,
    *,
    ordinal=1,
    status=MicrocycleLifecycleStatus.NOT_STARTED,
    start=None,
    expected_sessions=0,
    planned_posture="BUILD",
):
    planned_start = start or mesocycle.planned_start_date + timedelta(days=7 * (ordinal - 1))
    microcycle = Microcycle(
        mesocycle_id=mesocycle.id,
        ordinal=ordinal,
        planned_start_date=planned_start,
        planned_end_date=planned_start + timedelta(days=6),
        expected_sessions=expected_sessions,
        lifecycle_status=status,
        planned_posture=planned_posture,
    )
    db.add(microcycle)
    db.flush()
    return microcycle


def _seed_slot(
    db,
    microcycle,
    *,
    ordinal=1,
    slot_type=MicrocycleSlotType.TRAINING,
    resolution=MicrocycleSlotResolution.PENDING,
    resolution_source=None,
):
    slot = MicrocycleSlot(
        microcycle_id=microcycle.id,
        ordinal=ordinal,
        day_code=f"D{ordinal}",
        day_label=f"D{ordinal} Training",
        planned_date=microcycle.planned_start_date + timedelta(days=ordinal - 1),
        slot_type=slot_type,
        resolution=resolution,
        resolution_source=resolution_source,
    )
    db.add(slot)
    db.flush()
    return slot


def _slots_for(db, microcycle_id):
    return list(
        db.exec(
            select(MicrocycleSlot)
            .where(MicrocycleSlot.microcycle_id == microcycle_id)
            .order_by(MicrocycleSlot.ordinal)
        ).all()
    )


def test_active_microcycle_with_zero_slots_never_completes_vacuously(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        template = _seed_template(db)
        mesocycle = _seed_mesocycle(db, template, status=PlanStatus.ACTIVE)
        microcycle = _seed_microcycle(
            db,
            mesocycle,
            status=MicrocycleLifecycleStatus.ACTIVE,
            start=BASE_DATE,
            expected_sessions=0,
        )
        db.commit()
        microcycle_id = microcycle.id

        for _ in range(3):
            result = reconcile_current_training_state(db)
            saved = db.get(Microcycle, microcycle_id)

            assert result.blocked_reason is None
            assert saved.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE
            assert _slots_for(db, microcycle_id) == []


def test_future_successor_microcycle_does_not_block_current_active_microcycle(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db)
        template = _seed_template(db, name="headline regression template")
        macrocycle = _seed_macrocycle(db)
        mesocycle_1 = _seed_mesocycle(
            db,
            template,
            program=program,
            macrocycle=macrocycle,
            ordinal=1,
            start=BASE_DATE,
            status=PlanStatus.ACTIVE,
        )
        mesocycle_2 = _seed_mesocycle(
            db,
            template,
            program=program,
            macrocycle=macrocycle,
            ordinal=2,
            start=BASE_DATE + timedelta(days=28),
            status=PlanStatus.PLANNED,
        )
        active_microcycle = _seed_microcycle(
            db,
            mesocycle_1,
            status=MicrocycleLifecycleStatus.ACTIVE,
            start=BASE_DATE,
            expected_sessions=1,
        )
        _seed_slot(db, active_microcycle)
        future_microcycle = _seed_microcycle(
            db,
            mesocycle_2,
            status=MicrocycleLifecycleStatus.NOT_STARTED,
            start=mesocycle_2.planned_start_date,
            expected_sessions=0,
        )
        db.commit()
        active_microcycle_id = active_microcycle.id
        future_microcycle_id = future_microcycle.id

        result = reconcile_current_training_state(db)

        assert result.blocked_reason is None
        assert result.final_microcycle_id == active_microcycle_id
        assert result.final_mesocycle_id == mesocycle_1.id

        saved_future = db.get(Microcycle, future_microcycle_id)
        assert saved_future.lifecycle_status == MicrocycleLifecycleStatus.NOT_STARTED
        assert _slots_for(db, future_microcycle_id) == []


def test_waiting_microcycle_activates_when_local_today_reaches_start(monkeypatch):
    planned_start = BASE_DATE + timedelta(days=7)
    today = {"value": planned_start - timedelta(days=1)}
    monkeypatch.setattr(advancement, "local_today", lambda: today["value"])
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True))
        template = _seed_template(db, name="waiting activation template")
        mesocycle = _seed_mesocycle(db, template, program=program, start=planned_start)
        microcycle = _seed_microcycle(
            db,
            mesocycle,
            start=planned_start,
            expected_sessions=0,
        )
        db.commit()
        mesocycle_id = mesocycle.id
        microcycle_id = microcycle.id

        blocked = reconcile_current_training_state(db)

        assert blocked.blocked_reason == WAITING_FOR_MICROCYCLE_START
        assert db.get(Microcycle, microcycle_id).lifecycle_status == (
            MicrocycleLifecycleStatus.NOT_STARTED
        )
        assert _slots_for(db, microcycle_id) == []

        today["value"] = planned_start + timedelta(days=1)
        activated = reconcile_current_training_state(db)

        saved_microcycle = db.get(Microcycle, microcycle_id)
        saved_mesocycle = db.get(Mesocycle, mesocycle_id)
        slots = _slots_for(db, microcycle_id)
        training_slots = [
            slot for slot in slots if slot.slot_type == MicrocycleSlotType.TRAINING
        ]

        assert activated.blocked_reason is None
        assert saved_mesocycle.status == PlanStatus.ACTIVE
        assert saved_microcycle.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE
        assert len(slots) == 2
        assert len(training_slots) == 1
        assert training_slots[0].resolution == MicrocycleSlotResolution.PENDING


def test_program_drift_blocks_without_slots_then_clean_hash_activates(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True))
        template = _seed_template(db, name="program drift template")
        mesocycle = _seed_mesocycle(
            db,
            template,
            program=program,
            program_hash="wrong-prescription-hash",
        )
        microcycle = _seed_microcycle(db, mesocycle, start=BASE_DATE)
        db.commit()
        program_id = program.id
        mesocycle_id = mesocycle.id
        microcycle_id = microcycle.id

        drifted = reconcile_current_training_state(db)

        assert drifted.blocked_reason == PROGRAM_DRIFT
        assert db.get(Microcycle, microcycle_id).lifecycle_status == (
            MicrocycleLifecycleStatus.NOT_STARTED
        )
        assert _slots_for(db, microcycle_id) == []

        saved_program = db.get(Program, program_id)
        saved_mesocycle = db.get(Mesocycle, mesocycle_id)
        saved_mesocycle.program_prescription_hash = compute_program_prescription_hash(
            saved_program
        )
        db.add(saved_mesocycle)
        db.commit()

        activated = reconcile_current_training_state(db)

        assert activated.blocked_reason is None
        assert db.get(Microcycle, microcycle_id).lifecycle_status == (
            MicrocycleLifecycleStatus.ACTIVE
        )
        assert len(_slots_for(db, microcycle_id)) == 2


def test_ensure_first_microcycle_is_idempotent_and_rejects_inconsistent_existing_row():
    engine = _engine()

    with DbSession(engine) as db:
        template = _seed_template(db, name="ensure first template")
        mesocycle = _seed_mesocycle(db, template, start=BASE_DATE)

        first = ensure_first_microcycle_instantiated(db, mesocycle)
        second = ensure_first_microcycle_instantiated(db, mesocycle)

        rows = list(
            db.exec(
                select(Microcycle).where(Microcycle.mesocycle_id == mesocycle.id)
            ).all()
        )
        assert second.id == first.id
        assert len(rows) == 1

        inconsistent_mesocycle = _seed_mesocycle(
            db,
            template,
            ordinal=2,
            start=BASE_DATE + timedelta(days=14),
        )
        _seed_microcycle(
            db,
            inconsistent_mesocycle,
            start=inconsistent_mesocycle.planned_start_date,
            planned_posture="WRONG",
        )

        with pytest.raises(ValueError, match="planned_posture"):
            ensure_first_microcycle_instantiated(db, inconsistent_mesocycle)


def test_microcycle_instantiation_uses_zero_based_posture_indexing():
    engine = _engine()

    with DbSession(engine) as db:
        postures = ["ESTABLISH", "BUILD", "PUSH", "CONSOLIDATE"]
        template = _seed_template(db, postures=postures, name="posture indexing template")
        mesocycle = _seed_mesocycle(db, template, start=BASE_DATE)

        ensure_first_microcycle_instantiated(db, mesocycle)
        for ordinal in (2, 3, 4):
            advancement._ensure_microcycle_instantiated(db, mesocycle, ordinal)

        rows = list(
            db.exec(
                select(Microcycle)
                .where(Microcycle.mesocycle_id == mesocycle.id)
                .order_by(Microcycle.ordinal)
            ).all()
        )

        assert [row.ordinal for row in rows] == [1, 2, 3, 4]
        assert [row.planned_posture for row in rows] == postures
        assert [row.planned_start_date for row in rows] == [
            BASE_DATE + timedelta(days=7 * index)
            for index in range(4)
        ]


def test_successor_mesocycle_row_flips_macrocycle_planning_state_to_active(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True))
        template = _seed_template(db, name="macro successor template")
        macrocycle = _seed_macrocycle(
            db,
            start=BASE_DATE - timedelta(days=6),
            planning_state=MacroPlanningState.AWAITING_NEXT_MESOCYCLE,
        )
        mesocycle_1 = _seed_mesocycle(
            db,
            template,
            program=program,
            macrocycle=macrocycle,
            ordinal=1,
            start=BASE_DATE - timedelta(days=6),
            status=PlanStatus.ACTIVE,
        )
        microcycle = _seed_microcycle(
            db,
            mesocycle_1,
            status=MicrocycleLifecycleStatus.ACTIVE,
            start=BASE_DATE - timedelta(days=6),
            expected_sessions=1,
        )
        _seed_slot(
            db,
            microcycle,
            resolution=MicrocycleSlotResolution.COMPLETED,
            resolution_source=MicrocycleSlotResolutionSource.SESSION,
        )
        successor = _seed_mesocycle(
            db,
            template,
            program=program,
            macrocycle=macrocycle,
            ordinal=2,
            start=BASE_DATE + timedelta(days=1),
            status=PlanStatus.PLANNED,
        )
        db.commit()
        macrocycle_id = macrocycle.id
        successor_id = successor.id

        result = reconcile_current_training_state(db)

        saved_macrocycle = db.get(Macrocycle, macrocycle_id)
        successor_microcycle = db.exec(
            select(Microcycle).where(
                Microcycle.mesocycle_id == successor_id,
                Microcycle.ordinal == 1,
            )
        ).one()

        assert result.blocked_reason == WAITING_FOR_MICROCYCLE_START
        assert saved_macrocycle.planning_state == MacroPlanningState.ACTIVE
        assert successor_microcycle.lifecycle_status == MicrocycleLifecycleStatus.NOT_STARTED


def test_hash_mismatch_at_activation_leaves_zero_slot_rows(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, False, True))
        template = _seed_template(db, name="hash mismatch template")
        mesocycle = _seed_mesocycle(
            db,
            template,
            program=program,
            program_hash="stale-prescription-hash",
        )
        microcycle = _seed_microcycle(db, mesocycle, start=BASE_DATE)
        db.commit()
        microcycle_id = microcycle.id

        result = reconcile_current_training_state(db)

        assert result.blocked_reason == PROGRAM_DRIFT
        assert _slots_for(db, microcycle_id) == []
        assert db.exec(select(MicrocycleSlot)).all() == []


def test_all_rest_program_activation_raises_invalid_plan_configuration(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(True, True))
        template = _seed_template(db, name="all rest template")
        mesocycle = _seed_mesocycle(db, template, program=program)
        microcycle = _seed_microcycle(db, mesocycle, start=BASE_DATE)
        db.commit()
        microcycle_id = microcycle.id

        with pytest.raises(InvalidPlanConfigurationError):
            reconcile_current_training_state(db)

        assert db.get(Microcycle, microcycle_id).lifecycle_status == (
            MicrocycleLifecycleStatus.NOT_STARTED
        )
        assert _slots_for(db, microcycle_id) == []


def _log_reasons(db, entity_type=None):
    rows = list(db.exec(select(AdvancementLog)).all())
    return [
        row.reason
        for row in rows
        if entity_type is None or row.entity_type == entity_type
    ]


def test_activation_exception_leaves_no_partially_staged_slot_rows(monkeypatch):
    """Regression: a mid-activation failure must not leave slots to be committed later.

    Slots are db.add()-ed before the activation transaction commits. Without a
    rollback on the failure path those staged rows survive in the session and get
    flushed by the next unrelated commit -- persisting slots against a Microcycle
    that is still NOT_STARTED, which permanently poisons the zero-slot pending check.
    """
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    real_log_advancement = advancement._log_advancement

    def exploding_log_advancement(
        db, reconcile_run_id, entity_type, entity_id, reason, details_json=None
    ):
        if reason == "MESOCYCLE_ADVANCED":
            raise RuntimeError("boom mid-activation")
        return real_log_advancement(
            db, reconcile_run_id, entity_type, entity_id, reason, details_json
        )

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True, False))
        template = _seed_template(db, name="partial staging template")
        mesocycle = _seed_mesocycle(db, template, program=program, start=BASE_DATE)
        microcycle = _seed_microcycle(db, mesocycle, start=BASE_DATE)
        db.commit()
        microcycle_id = microcycle.id

        monkeypatch.setattr(advancement, "_log_advancement", exploding_log_advancement)
        with pytest.raises(RuntimeError, match="boom mid-activation"):
            reconcile_current_training_state(db)

        # An unrelated later commit on the same session must not resurrect the slots.
        db.commit()

    with DbSession(engine) as verify_db:
        assert _slots_for(verify_db, microcycle_id) == []
        assert verify_db.exec(select(MicrocycleSlot)).all() == []
        assert verify_db.get(Microcycle, microcycle_id).lifecycle_status == (
            MicrocycleLifecycleStatus.NOT_STARTED
        )


def test_activation_stores_topology_hash_compatible_with_program_hash_helper(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True, False))
        template = _seed_template(db, name="topology hash template")
        mesocycle = _seed_mesocycle(db, template, program=program, start=BASE_DATE)
        microcycle = _seed_microcycle(db, mesocycle, start=BASE_DATE)
        db.commit()
        microcycle_id = microcycle.id
        program_id = program.id

        result = reconcile_current_training_state(db)

        saved = db.get(Microcycle, microcycle_id)
        assert result.blocked_reason is None
        assert saved.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE
        assert saved.slot_topology_hash == compute_slot_topology_hash(
            db.get(Program, program_id)
        )
        assert "MICROCYCLE_ACTIVATED" in _log_reasons(db, entity_type="microcycle")


def test_template_cardinality_requires_exact_posture_to_week_match():
    engine = _engine()

    with DbSession(engine) as db:
        template = _seed_template(
            db,
            postures=("ESTABLISH", "BUILD", "BUILD", "PUSH", "PUSH", "CONSOLIDATE"),
            name="six posture template",
        )
        mesocycle = _seed_mesocycle(db, template, start=BASE_DATE)
        # Six postures, but the planned date range only spans four weeks.
        mesocycle.planned_end_date = BASE_DATE + timedelta(days=27)
        db.add(mesocycle)
        db.flush()

        with pytest.raises(ValueError, match="must match exactly"):
            advancement._validate_template_cardinality(mesocycle, template)

        with pytest.raises(ValueError, match="must match exactly"):
            ensure_first_microcycle_instantiated(db, mesocycle)

        # And the inverse direction: more weeks than postures.
        short_template = _seed_template(
            db, postures=("BUILD",), name="one posture template"
        )
        long_mesocycle = _seed_mesocycle(db, short_template, start=BASE_DATE)
        long_mesocycle.planned_end_date = BASE_DATE + timedelta(days=27)
        db.add(long_mesocycle)
        db.flush()

        with pytest.raises(ValueError, match="must match exactly"):
            advancement._validate_template_cardinality(long_mesocycle, short_template)


def test_ambiguous_multiple_pending_microcycles_warns_instead_of_raising(monkeypatch, caplog):
    """Two activation-eligible pending Microcycles must not crash the reconciler."""
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True))
        template = _seed_template(db, name="ambiguous pending template")
        # macrocycle_id is NULL on both, so _pending_activation_eligible returns True
        # unconditionally for each -- the exact data state that used to raise.
        earlier_mesocycle = _seed_mesocycle(db, template, program=program, start=BASE_DATE)
        later_mesocycle = _seed_mesocycle(
            db, template, program=program, start=BASE_DATE + timedelta(days=14)
        )
        earlier = _seed_microcycle(db, earlier_mesocycle, start=BASE_DATE)
        later = _seed_microcycle(
            db, later_mesocycle, start=BASE_DATE + timedelta(days=14)
        )
        db.commit()
        earlier_id = earlier.id
        later_id = later.id

        with caplog.at_level(logging.WARNING, logger="ironlog.engine.advancement"):
            selected = advancement._find_pending_microcycle(db)
            assert selected.id == earlier_id

            result = reconcile_current_training_state(db)

        assert "Multiple activation-eligible pending Microcycles" in caplog.text
        assert result.blocked_reason == WAITING_FOR_MICROCYCLE_START
        assert db.get(Microcycle, earlier_id).lifecycle_status == (
            MicrocycleLifecycleStatus.ACTIVE
        )
        assert db.get(Microcycle, later_id).lifecycle_status == (
            MicrocycleLifecycleStatus.NOT_STARTED
        )


def test_drift_beyond_four_days_infers_skips_and_exhausts_the_plan(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()
    start = BASE_DATE - timedelta(days=14)

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True))
        template = _seed_template(db, name="drift skip template")
        macrocycle = _seed_macrocycle(db, start=start)
        mesocycle = _seed_mesocycle(
            db,
            template,
            program=program,
            macrocycle=macrocycle,
            ordinal=1,
            start=start,
            status=PlanStatus.ACTIVE,
        )
        microcycle = _seed_microcycle(
            db,
            mesocycle,
            status=MicrocycleLifecycleStatus.ACTIVE,
            start=start,
            expected_sessions=1,
        )
        _seed_slot(db, microcycle)
        db.commit()
        microcycle_id = microcycle.id
        mesocycle_id = mesocycle.id
        macrocycle_id = macrocycle.id

        result = reconcile_current_training_state(db)

        saved_microcycle = db.get(Microcycle, microcycle_id)
        saved_mesocycle = db.get(Mesocycle, mesocycle_id)
        saved_macrocycle = db.get(Macrocycle, macrocycle_id)
        slots = _slots_for(db, microcycle_id)

        # planned_end_date was 8 days ago -> past the >4 day band, inferred skip pass.
        assert saved_microcycle.drift_days == 8
        assert slots[0].resolution == MicrocycleSlotResolution.SKIPPED
        assert slots[0].resolution_source == (
            MicrocycleSlotResolutionSource.INFERRED_BOUNDARY
        )
        assert saved_microcycle.lifecycle_status == MicrocycleLifecycleStatus.COMPLETE
        assert saved_mesocycle.status == PlanStatus.COMPLETE
        assert saved_macrocycle.planning_state == (
            MacroPlanningState.AWAITING_NEXT_MESOCYCLE
        )
        assert result.blocked_reason == AWAITING_NEXT_MESOCYCLE

        reasons = _log_reasons(db)
        assert "DRIFT_INFERRED_SKIP" in reasons
        assert "PLAN_EXHAUSTED" in reasons


def test_reconciler_never_marks_a_microcycle_incomplete_on_its_own(monkeypatch):
    monkeypatch.setattr(advancement, "local_today", lambda: BASE_DATE)
    engine = _engine()

    with DbSession(engine) as db:
        program = _seed_program(db, rest_flags=(False, True))
        template = _seed_template(db, name="no auto incomplete template")
        mesocycle = _seed_mesocycle(db, template, program=program, start=BASE_DATE)
        microcycle = _seed_microcycle(db, mesocycle, start=BASE_DATE)
        db.commit()
        microcycle_id = microcycle.id

        for _ in range(3):
            reconcile_current_training_state(db)
            statuses = [row.lifecycle_status for row in db.exec(select(Microcycle)).all()]
            assert MicrocycleLifecycleStatus.INCOMPLETE not in statuses

        marked = mark_microcycle_incomplete(db, microcycle_id, "OPERATOR_ABORT")

        assert marked.lifecycle_status == MicrocycleLifecycleStatus.INCOMPLETE
        assert "OPERATOR_ABORT" in _log_reasons(db, entity_type="microcycle")
