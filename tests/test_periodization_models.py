from datetime import date

from sqlmodel import SQLModel, Session as DbSession, create_engine, select

import ironlog.models  # noqa: F401 - register all tables
from ironlog.models.library import Movement
from ironlog.models.periodization import (
    BodyCompState, BodyCompStateValue, DeloadState, Macrocycle, Mesocycle,
    MesocycleTemplate, Microcycle, MicrocycleDriftStatus,
    MicrocycleLifecycleStatus, PlanStatus, RecoveryStatus, RecoveryStatusValue,
)
from ironlog.models.program import (
    MesoRotation, Program, ProgramDay, Tier, TierExercise, TierKind,
)
from ironlog.models.session import Session as WorkoutSession


def test_periodization_models_round_trip_all_schema_fields():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with DbSession(engine) as db:
        macro = Macrocycle(
            goal="Cut 20 lb while preserving lean mass",
            planned_start_date=date(2026, 9, 7),
            planned_end_date=date(2027, 1, 31),
            status=PlanStatus.ACTIVE,
        )
        template = MesocycleTemplate(
            name="APEX Bridge",
            postures=["ESTABLISH", "BUILD", "PUSH", "CONSOLIDATE"],
        )
        program = Program(name="Phase 1", phase="CUT", duration_weeks=8)
        movement = Movement(name="Test Press [PB]", base_name="Test Press")
        db.add_all([macro, template, program, movement])
        db.flush()

        day = ProgramDay(
            program_id=program.id,
            day_index=1,
            day_role="D1 Upper Push",
            is_rest=False,
        )
        db.add(day)
        db.flush()

        tier = Tier(
            program_day_id=day.id,
            tier_label="T1",
            tier_order=1,
            tier_kind=TierKind.T1_STRAIGHT,
        )
        db.add(tier)
        db.flush()

        tier_exercise = TierExercise(
            tier_id=tier.id,
            slot_id="d1_t1",
            movement_id=movement.id,
            exercise_order=1,
            tier_role="anchor",
            rep_low=5,
            rep_high=8,
        )
        db.add(tier_exercise)
        db.flush()

        mesocycle = Mesocycle(
            template_id=template.id,
            macrocycle_id=macro.id,
            ordinal=1,
            planned_start_date=date(2026, 9, 7),
            planned_end_date=date(2026, 10, 4),
            actual_start_date=date(2026, 9, 8),
            status=PlanStatus.ACTIVE,
        )
        db.add(mesocycle)
        db.flush()

        microcycle = Microcycle(
            mesocycle_id=mesocycle.id,
            ordinal=1,
            planned_start_date=date(2026, 9, 7),
            planned_end_date=date(2026, 9, 13),
            actual_start_date=date(2026, 9, 8),
            expected_sessions=5,
            completed_sessions=2,
            lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
            drift_status=MicrocycleDriftStatus.EXTENDED,
            drift_days=1,
            planned_posture="BUILD",
            effective_posture="BUILD",
        )
        db.add(microcycle)
        db.flush()

        body_comp = BodyCompState(
            state=BodyCompStateValue.CUT,
            effective_from=date(2026, 9, 7),
            notes="Initial cut timeline",
        )
        recovery = RecoveryStatus(
            as_of_date=date(2026, 9, 8),
            status=RecoveryStatusValue.CAUTION,
            inputs_snapshot={"rhr_down": False, "sleep_ok": True},
        )
        deload = DeloadState(
            microcycle_id=microcycle.id,
            active=True,
            triggered_at=date(2026, 9, 10),
            trigger_reason="persistent suppressed recovery plus missed reps",
        )
        rotation = MesoRotation(
            tier_exercise_id=tier_exercise.id,
            meso_number=2,
            mesocycle_id=mesocycle.id,
            movement_id=movement.id,
            rep_low=6,
            rep_high=10,
        )
        workout_session = WorkoutSession(
            date=date(2026, 9, 8),
            day_role="D1 Upper Push",
            phase="CUT",
            prescription_snapshot={
                "macrocycle_id": macro.id,
                "mesocycle_id": mesocycle.id,
                "microcycle_id": microcycle.id,
                "planned_posture": "BUILD",
                "body_comp_state": "CUT",
                "recovery_status": "CAUTION",
                "deload_state": {"active": True, "trigger_reason": deload.trigger_reason},
                "resolved_envelope": {
                    "rpe_cap": 8.0,
                    "volume_multiplier": 0.9,
                    "progression_mode": "HOLD_IF_BORDERLINE",
                    "optional_work_eligible": False,
                },
                "resolver_policy_version": "test",
            },
        )
        db.add_all([body_comp, recovery, deload, rotation, workout_session])
        db.commit()

        saved_macro = db.exec(select(Macrocycle)).one()
        saved_template = db.exec(select(MesocycleTemplate)).one()
        saved_mesocycle = db.exec(select(Mesocycle)).one()
        saved_microcycle = db.exec(select(Microcycle)).one()
        saved_body_comp = db.exec(select(BodyCompState)).one()
        saved_recovery = db.exec(select(RecoveryStatus)).one()
        saved_deload = db.exec(select(DeloadState)).one()
        saved_rotation = db.exec(select(MesoRotation)).one()
        saved_session = db.exec(select(WorkoutSession)).one()

    assert saved_macro.goal == "Cut 20 lb while preserving lean mass"
    assert saved_macro.status == PlanStatus.ACTIVE
    assert saved_template.name == "APEX Bridge"
    assert saved_template.postures == ["ESTABLISH", "BUILD", "PUSH", "CONSOLIDATE"]
    assert saved_mesocycle.template_id == saved_template.id
    assert saved_mesocycle.macrocycle_id == saved_macro.id
    assert saved_mesocycle.actual_end_date is None
    assert saved_microcycle.ordinal == 1
    assert saved_microcycle.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE
    assert saved_microcycle.drift_status == MicrocycleDriftStatus.EXTENDED
    assert saved_microcycle.effective_posture == "BUILD"
    assert saved_body_comp.state == BodyCompStateValue.CUT
    assert saved_body_comp.effective_to is None
    assert saved_recovery.status == RecoveryStatusValue.CAUTION
    assert saved_recovery.inputs_snapshot == {"rhr_down": False, "sleep_ok": True}
    assert saved_deload.microcycle_id == saved_microcycle.id
    assert saved_deload.active is True
    assert saved_deload.resolved_at is None
    assert saved_rotation.meso_number == 2
    assert saved_rotation.mesocycle_id == saved_mesocycle.id
    assert saved_session.prescription_snapshot["microcycle_id"] == saved_microcycle.id
    assert saved_session.prescription_snapshot["resolved_envelope"]["rpe_cap"] == 8.0
