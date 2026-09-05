from datetime import date, datetime
import pytest
from unittest.mock import patch
import sys

from sqlmodel import select
from sqlalchemy.engine import Engine

from ironlog.models.periodization import (
    Microcycle, Mesocycle, Macrocycle, MicrocycleLifecycleStatus,
    MicrocycleSlot, MicrocycleSlotType, MicrocycleSlotResolution,
    MicrocycleSlotResolutionSource
)
from ironlog.models.program import Program, ProgramDay
from ironlog.models.session import Session as IronSession, SessionStatus
from ironlog.models.enums import SessionPlanStatus
from ironlog.engine.program_hash import compute_slot_topology_hash

from scripts.bootstrap_microcycle_one import main, run_bootstrap

def _setup_base_data(gen_db, all_rest=False):
    # gen_db already has a Program with ProgramDays
    program = gen_db.exec(select(Program)).first()
    
    if all_rest:
        days = gen_db.exec(select(ProgramDay).where(ProgramDay.program_id == program.id)).all()
        for day in days:
            day.is_rest = True
            gen_db.add(day)
        gen_db.commit()

    macro = Macrocycle(goal="Test Macro")
    gen_db.add(macro)
    gen_db.commit()

    meso = Mesocycle(
        template_id=1,  # dummy
        macrocycle_id=macro.id,
        program_id=program.id,
        ordinal=1,
        planned_start_date=date(2026, 1, 1),
        planned_end_date=date(2026, 1, 28),
    )
    gen_db.add(meso)
    gen_db.commit()

    micro = Microcycle(
        mesocycle_id=meso.id,
        ordinal=1,
        planned_start_date=date(2026, 1, 1),
        planned_end_date=date(2026, 1, 7),
        expected_sessions=4,
        lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
        planned_posture="Hypertrophy"
    )
    gen_db.add(micro)
    gen_db.commit()

    return program, meso, micro

def _run_cli(engine: Engine, *args):
    with patch("sys.argv", ["scripts/bootstrap_microcycle_one.py", *args]), \
         patch("scripts.bootstrap_microcycle_one.default_engine", engine):
        try:
            main()
        except SystemExit as e:
            return e.code
    return 0

def test_success_and_hash(gen_db):
    program, meso, micro = _setup_base_data(gen_db)
    
    # Run the script
    exit_code = _run_cli(gen_db.bind, "--apply")
    assert exit_code == 0
    
    gen_db.expire_all()
    
    slots = gen_db.exec(select(MicrocycleSlot)).all()
    assert len(slots) > 0
    
    updated_micro = gen_db.exec(select(Microcycle).where(Microcycle.id == micro.id)).one()
    expected_hash = compute_slot_topology_hash(program)
    assert updated_micro.slot_topology_hash == expected_hash

def test_unmatched_session_halts_and_rollbacks(gen_db):
    program, meso, micro = _setup_base_data(gen_db)
    
    # Add a session with an invalid day_role
    sess = IronSession(
        date=date(2026, 1, 1),
        day_role="Invalid Day Role",
        phase="CUT",
        prescription_snapshot={"microcycle_id": micro.id},
        status=SessionStatus.PLANNED
    )
    gen_db.add(sess)
    gen_db.commit()
    
    exit_code = _run_cli(gen_db.bind, "--apply")
    assert exit_code != 0
    
    gen_db.expire_all()
    slots = gen_db.exec(select(MicrocycleSlot)).all()
    assert len(slots) == 0

def test_all_rest_program_halts(gen_db):
    program, meso, micro = _setup_base_data(gen_db, all_rest=True)
    
    exit_code = _run_cli(gen_db.bind, "--apply")
    assert exit_code != 0
    
    gen_db.expire_all()
    slots = gen_db.exec(select(MicrocycleSlot)).all()
    assert len(slots) == 0

def test_session_status_resolutions(gen_db):
    program, meso, micro = _setup_base_data(gen_db)
    
    program_days = gen_db.exec(select(ProgramDay).where(ProgramDay.program_id == program.id)).all()
    valid_day_role_1 = program_days[0].day_role
    valid_day_role_2 = program_days[1].day_role if len(program_days) > 1 else program_days[0].day_role
    
    sess_completed = IronSession(
        date=date(2026, 1, 1),
        day_role=valid_day_role_1,
        phase="CUT",
        prescription_snapshot={"microcycle_id": micro.id},
        status=SessionStatus.COMPLETED,
        approved_at=datetime.utcnow()
    )
    sess_planned = IronSession(
        date=date(2026, 1, 2),
        day_role=valid_day_role_2,
        phase="CUT",
        prescription_snapshot={"microcycle_id": micro.id},
        status=SessionStatus.PLANNED
    )
    gen_db.add(sess_completed)
    gen_db.add(sess_planned)
    gen_db.commit()
    
    exit_code = _run_cli(gen_db.bind, "--apply")
    assert exit_code == 0
    
    gen_db.expire_all()
    
    slot_comp = gen_db.exec(select(MicrocycleSlot).where(MicrocycleSlot.day_label == valid_day_role_1)).one()
    assert slot_comp.session_id == sess_completed.id
    assert slot_comp.resolution == MicrocycleSlotResolution.COMPLETED
    assert slot_comp.resolution_source == MicrocycleSlotResolutionSource.SESSION
    
    slot_plan = gen_db.exec(select(MicrocycleSlot).where(MicrocycleSlot.day_label == valid_day_role_2)).first()
    if valid_day_role_1 != valid_day_role_2:
        assert slot_plan.session_id == sess_planned.id
        assert slot_plan.resolution == MicrocycleSlotResolution.PENDING
        
def test_usage_error(gen_db):
    # No args
    exit_code = _run_cli(gen_db.bind)
    assert exit_code != 0
    
    # Both args
    exit_code = _run_cli(gen_db.bind, "--apply", "--dry-run")
    assert exit_code != 0

def test_dry_run_no_changes(gen_db):
    program, meso, micro = _setup_base_data(gen_db)
    
    slots_before = gen_db.exec(select(MicrocycleSlot)).all()
    assert len(slots_before) == 0
    
    exit_code = _run_cli(gen_db.bind, "--dry-run")
    assert exit_code == 0
    
    gen_db.expire_all()
    slots_after = gen_db.exec(select(MicrocycleSlot)).all()
    assert len(slots_after) == 0
    
    updated_micro = gen_db.exec(select(Microcycle).where(Microcycle.id == micro.id)).one()
    assert updated_micro.slot_topology_hash is None

def test_existing_slots_halt(gen_db):
    program, meso, micro = _setup_base_data(gen_db)
    
    dummy_slot = MicrocycleSlot(
        microcycle_id=micro.id, ordinal=1, day_code="D1", day_label="Dummy",
        planned_date=date(2026, 1, 1), slot_type=MicrocycleSlotType.TRAINING
    )
    gen_db.add(dummy_slot)
    gen_db.commit()
    
    exit_code = _run_cli(gen_db.bind, "--apply")
    assert exit_code != 0
    
    gen_db.expire_all()
    slots = gen_db.exec(select(MicrocycleSlot)).all()
    assert len(slots) == 1

def test_unrelated_session_untouched(gen_db):
    program, meso, micro = _setup_base_data(gen_db)
    
    sess_no_snap = IronSession(
        date=date(2026, 1, 1), day_role="Role", phase="CUT", status=SessionStatus.PLANNED,
        plan_status=SessionPlanStatus.LEGACY
    )
    sess_diff_micro = IronSession(
        date=date(2026, 1, 2), day_role="Role", phase="CUT", status=SessionStatus.PLANNED,
        prescription_snapshot={"microcycle_id": 999}, plan_status=SessionPlanStatus.LEGACY
    )
    gen_db.add(sess_no_snap)
    gen_db.add(sess_diff_micro)
    gen_db.commit()
    
    exit_code = _run_cli(gen_db.bind, "--apply")
    assert exit_code == 0
    
    gen_db.expire_all()
    
    s1 = gen_db.exec(select(IronSession).where(IronSession.id == sess_no_snap.id)).one()
    s2 = gen_db.exec(select(IronSession).where(IronSession.id == sess_diff_micro.id)).one()
    
    assert s1.plan_status == SessionPlanStatus.LEGACY
    assert s2.plan_status == SessionPlanStatus.LEGACY
