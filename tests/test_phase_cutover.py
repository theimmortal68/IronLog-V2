import pytest
from datetime import date
from sqlmodel import Session as DbSession, select

from ironlog.models.library import EngineState, DailyReadiness
from ironlog.models.session import Session as TrainingSession
from ironlog.models.periodization import (
    BodyCompState, RecoveryStatus, RecoveryStatusValue, Macrocycle,
    Mesocycle, Microcycle
)
from ironlog.models.program import MesoRotation
from ironlog.models.enums import Phase
from scripts.migrate_phase_to_periodization import migrate


def _setup_db(session, phase, seed_readiness=True):
    session.add(EngineState(id=1, current_phase=phase))
    
    if seed_readiness:
        # Seed some DailyReadiness to trigger NORMAL (requires 5 readings for MIN_READINGS)
        session.add(DailyReadiness(date=date(2026, 8, 30), sleep_ok=True, subjective_ok=True))
        session.add(DailyReadiness(date=date(2026, 8, 31), sleep_ok=True, subjective_ok=True))
        session.add(DailyReadiness(date=date(2026, 9, 1), sleep_ok=True, subjective_ok=True))
        session.add(DailyReadiness(date=date(2026, 9, 2), sleep_ok=True, subjective_ok=True))
        session.add(DailyReadiness(date=date(2026, 9, 3), sleep_ok=True, subjective_ok=True))
    
    # Seed a Session for shadow validation
    session.add(TrainingSession(date=date(2026, 9, 3), day_role="Upper A", phase=phase.value))
    
    # Seed a MesoRotation
    from ironlog.models.program import TierExercise, Tier, ProgramDay, Program
    from ironlog.models.library import Movement
    prog = Program(name="Test", phase="1", duration_weeks=4)
    session.add(prog)
    session.flush()
    pday = ProgramDay(program_id=prog.id, day_index=1, day_role="D1")
    session.add(pday)
    session.flush()
    tier = Tier(program_day_id=pday.id, tier_label="T1", tier_order=1, tier_kind="T1_STRAIGHT")
    session.add(tier)
    session.flush()
    mvmt = Movement(name="Squat", base_name="Squat")
    session.add(mvmt)
    session.flush()
    te = TierExercise(tier_id=tier.id, slot_id="d1_t1", movement_id=mvmt.id, exercise_order=1, tier_role="anchor")
    session.add(te)
    session.flush()
    rot = MesoRotation(tier_exercise_id=te.id, meso_number=2, movement_id=mvmt.id)
    session.add(rot)
    session.commit()


def test_migrate_cut(db_session):
    _setup_db(db_session, Phase.CUT)
    plan = migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))
    assert plan["body_comp_state"] == "CUT"
    assert len(plan["shadow_validation"]) == 1

def test_migrate_stab(db_session):
    _setup_db(db_session, Phase.STAB)
    plan = migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))
    assert plan["body_comp_state"] == "MAINTENANCE"

def test_migrate_calibration_requires_arg(db_session):
    _setup_db(db_session, Phase.CALIBRATION)
    with pytest.raises(ValueError, match="You must provide --calibration-maps-to"):
        migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))

def test_migrate_calibration_with_arg(db_session):
    _setup_db(db_session, Phase.CALIBRATION)
    plan = migrate(db_session, apply=False, current_microcycle_ordinal=1, calibration_maps_to="MAINTENANCE", as_of=date(2026, 9, 4))
    assert plan["body_comp_state"] == "MAINTENANCE"

def test_migrate_rebuild_requires_arg(db_session):
    _setup_db(db_session, Phase.REBUILD)
    with pytest.raises(ValueError, match="You must provide --rebuild-maps-to"):
        migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))

def test_migrate_rebuild_with_arg(db_session):
    _setup_db(db_session, Phase.REBUILD)
    plan = migrate(db_session, apply=False, current_microcycle_ordinal=1, rebuild_maps_to="GAIN", as_of=date(2026, 9, 4))
    assert plan["body_comp_state"] == "GAIN"

def test_migrate_recovery_status_computation(db_session):
    _setup_db(db_session, Phase.STAB, seed_readiness=False)
    # Poor sleep, subjective okay -> CAUTION
    db_session.add(DailyReadiness(date=date(2026, 9, 1), sleep_ok=False, subjective_ok=True))
    db_session.add(DailyReadiness(date=date(2026, 9, 2), sleep_ok=False, subjective_ok=True))
    db_session.add(DailyReadiness(date=date(2026, 9, 3), sleep_ok=False, subjective_ok=True))
    db_session.add(DailyReadiness(date=date(2026, 9, 4), sleep_ok=False, subjective_ok=True))
    db_session.add(DailyReadiness(date=date(2026, 9, 5), sleep_ok=False, subjective_ok=True))
    db_session.commit()
    
    plan = migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 6))
    assert plan["recovery_status"] == RecoveryStatusValue.CAUTION.value

def test_migrate_dry_run_is_idempotent(db_session):
    _setup_db(db_session, Phase.CUT)
    plan1 = migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))
    plan2 = migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))
    assert plan1 == plan2

def test_migrate_apply_writes_data(db_session):
    _setup_db(db_session, Phase.CUT)
    migrate(db_session, apply=True, current_microcycle_ordinal=3, as_of=date(2026, 9, 4))
    
    # Assert DB writes
    assert db_session.exec(select(Macrocycle)).first() is not None
    meso = db_session.exec(select(Mesocycle)).first()
    assert meso is not None
    micro = db_session.exec(select(Microcycle)).first()
    assert micro is not None
    assert micro.ordinal == 3
    
    bcs = db_session.exec(select(BodyCompState)).first()
    assert bcs.state.value == "CUT"
    
    rs = db_session.exec(select(RecoveryStatus)).first()
    assert rs.status.value == RecoveryStatusValue.NORMAL.value
    
    # Assert MesoRotation was backfilled
    rot = db_session.exec(select(MesoRotation)).first()
    assert rot.mesocycle_id == meso.id

def test_shadow_validation_pass(db_session):
    _setup_db(db_session, Phase.CUT)
    plan = migrate(db_session, apply=False, current_microcycle_ordinal=1, as_of=date(2026, 9, 4))
    assert len(plan["shadow_validation"]) == 1
    assert "Simulated" not in plan["shadow_validation"][0]["simulated_envelope"] # It's a formatted string
    assert "RPE Cap:" in plan["shadow_validation"][0]["simulated_envelope"]

@pytest.fixture
def db_session():
    from sqlmodel import create_engine, Session as DbSession, SQLModel
    from ironlog.models import library, session as ss, periodization, program
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with DbSession(engine) as session:
        yield session
