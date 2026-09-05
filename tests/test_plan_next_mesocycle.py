import pytest
from datetime import date, timedelta
from sqlmodel import Session, select, SQLModel, create_engine

from ironlog.models.periodization import (
    Macrocycle, Mesocycle, MesocycleTemplate, Microcycle, AdvancementLog,
    MacroPlanningState, PlanStatus, MicrocycleLifecycleStatus, MicrocycleDriftStatus,
    MicrocycleSlotType, MicrocycleSlotResolution
)
from ironlog.models.program import Program
from scripts.plan_next_mesocycle import main

@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return eng

@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session

@pytest.fixture
def base_data(db: Session):
    macrocycle = Macrocycle(goal="Test Macrocycle", planning_state=MacroPlanningState.AWAITING_NEXT_MESOCYCLE)
    db.add(macrocycle)
    
    template = MesocycleTemplate(name="Test Template", postures=["HYPERTROPHY", "HYPERTROPHY", "HYPERTROPHY", "STRENGTH"])
    db.add(template)
    
    program = Program(name="Test Program", phase="TEST", duration_weeks=4)
    db.add(program)
    
    db.commit()
    return macrocycle.id, template.id, program.id

def test_no_predecessor(db: Session, engine, base_data):
    macro_id, tmpl_id, prog_id = base_data
    
    main(["--macrocycle", str(macro_id), "--template", str(tmpl_id), "--program", str(prog_id), "--ordinal", "1"], engine_override=engine)
    
    meso = db.exec(select(Mesocycle).where(Mesocycle.macrocycle_id == macro_id)).first()
    assert meso is not None
    assert meso.ordinal == 1
    assert meso.status == PlanStatus.PLANNED
    
    micro = db.exec(select(Microcycle).where(Microcycle.mesocycle_id == meso.id)).first()
    assert micro is not None
    assert micro.ordinal == 1
    
    macro = db.get(Macrocycle, macro_id)
    assert macro.planning_state == MacroPlanningState.ACTIVE
    
    log = db.exec(select(AdvancementLog).where(AdvancementLog.reason == "SUCCESSOR_PLANNED")).first()
    assert log is not None

def test_predecessor_active(db: Session, engine, base_data):
    macro_id, tmpl_id, prog_id = base_data
    
    pred = Mesocycle(
        macrocycle_id=macro_id,
        template_id=tmpl_id,
        program_id=prog_id,
        ordinal=1,
        planned_start_date=date.today() - timedelta(days=28),
        planned_end_date=date.today() - timedelta(days=1),
        status=PlanStatus.ACTIVE
    )
    db.add(pred)
    macro = db.get(Macrocycle, macro_id)
    macro.planning_state = MacroPlanningState.ACTIVE
    db.add(macro)
    db.commit()
    
    main(["--macrocycle", str(macro_id), "--template", str(tmpl_id), "--program", str(prog_id)], engine_override=engine)
    
    succ = db.exec(select(Mesocycle).where(Mesocycle.macrocycle_id == macro_id, Mesocycle.ordinal == 2)).first()
    assert succ is not None
    assert succ.status == PlanStatus.PLANNED
    
    micros = db.exec(select(Microcycle).where(Microcycle.mesocycle_id == succ.id)).all()
    assert len(micros) == 0  # No premature instantiation
    
def test_predecessor_complete(db: Session, engine, base_data):
    macro_id, tmpl_id, prog_id = base_data
    
    pred = Mesocycle(
        macrocycle_id=macro_id,
        template_id=tmpl_id,
        program_id=prog_id,
        ordinal=1,
        planned_start_date=date.today() - timedelta(days=28),
        planned_end_date=date.today() - timedelta(days=1),
        status=PlanStatus.COMPLETE
    )
    db.add(pred)
    db.commit()
    
    main(["--macrocycle", str(macro_id), "--template", str(tmpl_id), "--program", str(prog_id)], engine_override=engine)
    
    succ = db.exec(select(Mesocycle).where(Mesocycle.macrocycle_id == macro_id, Mesocycle.ordinal == 2)).first()
    assert succ is not None
    assert succ.status == PlanStatus.PLANNED
    
    micros = db.exec(select(Microcycle).where(Microcycle.mesocycle_id == succ.id)).all()
    assert len(micros) == 1
    assert micros[0].ordinal == 1

def test_idempotent_run(db: Session, engine, base_data, capsys):
    macro_id, tmpl_id, prog_id = base_data
    
    main(["--macrocycle", str(macro_id), "--template", str(tmpl_id), "--program", str(prog_id), "--ordinal", "1"], engine_override=engine)
    
    mesos_before = db.exec(select(Mesocycle).where(Mesocycle.macrocycle_id == macro_id)).all()
    assert len(mesos_before) == 1
    
    micros_before = db.exec(select(Microcycle).where(Microcycle.mesocycle_id == mesos_before[0].id)).all()
    assert len(micros_before) == 1
    
    # Run a second time
    main(["--macrocycle", str(macro_id), "--template", str(tmpl_id), "--program", str(prog_id), "--ordinal", "1"], engine_override=engine)
    
    mesos_after = db.exec(select(Mesocycle).where(Mesocycle.macrocycle_id == macro_id)).all()
    assert len(mesos_after) == 1
    
    micros_after = db.exec(select(Microcycle).where(Microcycle.mesocycle_id == mesos_after[0].id)).all()
    assert len(micros_after) == 1

def test_cardinality_mismatch(db: Session, engine, base_data):
    macro_id, tmpl_id, prog_id = base_data
    
    start = date.today()
    # 4 postures = 4 weeks (28 days). We supply 6 weeks (42 days)
    end = start + timedelta(days=41)
    
    with pytest.raises(SystemExit) as exc_info:
        main([
            "--macrocycle", str(macro_id), 
            "--template", str(tmpl_id), 
            "--program", str(prog_id),
            "--start-date", start.isoformat(),
            "--end-date", end.isoformat()
        ], engine_override=engine)
    
    assert exc_info.value.code != 0
    mesos = db.exec(select(Mesocycle).where(Mesocycle.macrocycle_id == macro_id)).all()
    assert len(mesos) == 0

def test_macrocycle_state_flips_and_log_created(db: Session, engine, base_data):
    macro_id, tmpl_id, prog_id = base_data
    
    main(["--macrocycle", str(macro_id), "--template", str(tmpl_id), "--program", str(prog_id)], engine_override=engine)
    
    macro = db.get(Macrocycle, macro_id)
    assert macro.planning_state == MacroPlanningState.ACTIVE
    
    logs = db.exec(select(AdvancementLog).where(AdvancementLog.reason == "SUCCESSOR_PLANNED")).all()
    assert len(logs) == 1
    
    bad_logs = db.exec(select(AdvancementLog).where(AdvancementLog.reason == "MESOCYCLE_ADVANCED")).all()
    assert len(bad_logs) == 0
