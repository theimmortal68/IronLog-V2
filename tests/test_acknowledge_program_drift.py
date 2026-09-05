import sys
from datetime import date
from unittest.mock import patch
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.library import Movement
from ironlog.models.periodization import Mesocycle, MesocycleTemplate, Microcycle, AdvancementLog, MicrocycleLifecycleStatus, PlanStatus, MicrocycleDriftStatus
from ironlog.models.program import Program, ProgramDay, Tier, TierExercise, TierKind
from ironlog.engine.program_hash import compute_program_prescription_hash, compute_slot_topology_hash
from scripts.acknowledge_program_drift import main
import ironlog.models

# NO from __future__ import annotations (project-wide constraint).

def run_main(args, monkeypatch, capsys, engine):
    with patch("scripts.acknowledge_program_drift.engine", engine):
        try:
            main(args)
            return 0, capsys.readouterr()
        except SystemExit as e:
            return e.code, capsys.readouterr()

def setup_data(db, with_active_microcycle=False):
    movement = db.exec(select(Movement)).first()

    program = Program(name="Test Program", phase="P1", duration_weeks=4)
    db.add(program)
    db.flush()

    day = ProgramDay(program_id=program.id, day_index=1, day_role="D1", is_rest=False)
    db.add(day)
    db.flush()

    tier = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    db.add(tier)
    db.flush()

    exercise = TierExercise(
        tier_id=tier.id,
        slot_id="d1_t1",
        exercise_order=1,
        movement_id=movement.id,
        tier_role="anchor",
        objective="PROGRESS",
        progression_rule="RPE_8_STANDARD",
        rep_low=5,
        rep_high=8
    )
    db.add(exercise)
    db.flush()

    template = MesocycleTemplate(name="Test Template")
    db.add(template)
    db.flush()

    mesocycle = Mesocycle(
        template_id=template.id,
        program_id=program.id,
        planned_start_date=date(2026, 1, 1),
        planned_end_date=date(2026, 1, 28),
        status=PlanStatus.PLANNED,
        program_prescription_hash="old_hash_dummy"
    )
    db.add(mesocycle)
    db.flush()

    if with_active_microcycle:
        microcycle = Microcycle(
            mesocycle_id=mesocycle.id,
            ordinal=1,
            planned_start_date=date(2026, 1, 1),
            planned_end_date=date(2026, 1, 7),
            expected_sessions=1,
            lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
            drift_status=MicrocycleDriftStatus.ON_TIME,
            slot_topology_hash=compute_slot_topology_hash(program),
            planned_posture="BUILD",
            effective_posture="BUILD"
        )
        db.add(microcycle)
        db.flush()

    db.commit()
    db.refresh(mesocycle)
    db.refresh(program)
    
    return program, mesocycle

def test_no_active_microcycle(gen_db, monkeypatch, capsys):
    program, mesocycle = setup_data(gen_db, with_active_microcycle=False)
    current_hash = compute_program_prescription_hash(program)

    code, out = run_main(["--mesocycle", str(mesocycle.id), "--accept-current-program-revision"], monkeypatch, capsys, gen_db.bind)

    assert code == 0
    gen_db.refresh(mesocycle)
    assert mesocycle.program_prescription_hash == current_hash

    logs = gen_db.exec(select(AdvancementLog).where(AdvancementLog.entity_id == mesocycle.id)).all()
    assert len(logs) == 1
    assert logs[0].entity_type == "mesocycle"
    assert logs[0].entity_id == mesocycle.id
    assert logs[0].reason == "PROGRAM_DRIFT_ACKNOWLEDGED"
    assert logs[0].details_json == {"old_hash": "old_hash_dummy", "new_hash": current_hash}


def test_without_accept_current_program_revision(gen_db, monkeypatch, capsys):
    program, mesocycle = setup_data(gen_db, with_active_microcycle=False)

    code, out = run_main(["--mesocycle", str(mesocycle.id)], monkeypatch, capsys, gen_db.bind)

    assert code == 0
    gen_db.refresh(mesocycle)
    assert mesocycle.program_prescription_hash == "old_hash_dummy"

    logs = gen_db.exec(select(AdvancementLog).where(AdvancementLog.entity_id == mesocycle.id)).all()
    assert len(logs) == 0


def test_refuses_active_microcycle_with_topology_change(gen_db, monkeypatch, capsys):
    program, mesocycle = setup_data(gen_db, with_active_microcycle=True)
    
    day = gen_db.exec(select(ProgramDay).where(ProgramDay.program_id == program.id)).first()
    day.is_rest = True
    gen_db.add(day)
    gen_db.commit()
    gen_db.refresh(program)

    code, out = run_main(["--mesocycle", str(mesocycle.id), "--accept-current-program-revision"], monkeypatch, capsys, gen_db.bind)

    assert code != 0
    assert "REFUSE" in out.err

    gen_db.refresh(mesocycle)
    assert mesocycle.program_prescription_hash == "old_hash_dummy"

    logs = gen_db.exec(select(AdvancementLog).where(AdvancementLog.entity_id == mesocycle.id)).all()
    assert len(logs) == 0


def test_accepts_active_microcycle_prescription_change(gen_db, monkeypatch, capsys):
    program, mesocycle = setup_data(gen_db, with_active_microcycle=True)
    
    exercise = gen_db.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).first()
    exercise.rep_low = 6
    gen_db.add(exercise)
    gen_db.commit()
    gen_db.refresh(program)

    current_hash = compute_program_prescription_hash(program)

    code, out = run_main(["--mesocycle", str(mesocycle.id), "--accept-current-program-revision"], monkeypatch, capsys, gen_db.bind)

    assert code == 0
    gen_db.refresh(mesocycle)
    assert mesocycle.program_prescription_hash == current_hash

    logs = gen_db.exec(select(AdvancementLog).where(AdvancementLog.entity_id == mesocycle.id)).all()
    assert len(logs) == 1
    assert logs[0].reason == "PROGRAM_DRIFT_ACKNOWLEDGED"
    assert logs[0].details_json == {"old_hash": "old_hash_dummy", "new_hash": current_hash}
