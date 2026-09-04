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
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    assert plan["body_comp_state"] == "CUT"
    assert len(plan["shadow_validation"]) == 1

def test_migrate_stab(db_session):
    _setup_db(db_session, Phase.STAB)
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    assert plan["body_comp_state"] == "MAINTENANCE"

def test_migrate_calibration_requires_arg(db_session):
    _setup_db(db_session, Phase.CALIBRATION)
    with pytest.raises(ValueError, match="You must provide --calibration-maps-to"):
        migrate(
            db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
            mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
        )

def test_migrate_calibration_with_arg(db_session):
    _setup_db(db_session, Phase.CALIBRATION)
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", calibration_maps_to="MAINTENANCE", as_of=date(2026, 9, 4)
    )
    assert plan["body_comp_state"] == "MAINTENANCE"

def test_migrate_rebuild_requires_arg(db_session):
    _setup_db(db_session, Phase.REBUILD)
    with pytest.raises(ValueError, match="You must provide --rebuild-maps-to"):
        migrate(
            db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
            mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
        )

def test_migrate_rebuild_with_arg(db_session):
    _setup_db(db_session, Phase.REBUILD)
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", rebuild_maps_to="GAIN", as_of=date(2026, 9, 4)
    )
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
    
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 6)
    )
    assert plan["recovery_status"] == RecoveryStatusValue.CAUTION.value

def test_migrate_dry_run_is_idempotent(db_session):
    _setup_db(db_session, Phase.CUT)
    plan1 = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    plan2 = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    assert plan1 == plan2

def test_migrate_apply_writes_data_and_matches_plan(db_session):
    _setup_db(db_session, Phase.CUT)
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=2, current_microcycle_ordinal=3, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    migrate(
        db_session, apply=True, current_mesocycle_ordinal=2, current_microcycle_ordinal=3, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    
    # Assert DB writes exactly match plan
    macro = db_session.exec(select(Macrocycle)).first()
    assert macro is not None
    assert macro.goal == plan["macrocycle_goal"]
    assert macro.planned_start_date.isoformat() == plan["planned_start_date"]
    
    meso = db_session.exec(select(Mesocycle)).first()
    assert meso is not None
    assert meso.ordinal == plan["mesocycle_ordinal"]
    assert meso.planned_start_date.isoformat() == plan["planned_start_date"]
    assert meso.planned_end_date.isoformat() == plan["planned_end_date"]
    
    micro = db_session.exec(select(Microcycle)).first()
    assert micro is not None
    assert micro.ordinal == 3
    assert micro.expected_sessions == plan["expected_sessions"]
    assert micro.planned_posture == plan["seed_posture"]
    
    bcs = db_session.exec(select(BodyCompState)).first()
    assert bcs.state.value == plan["body_comp_state"]
    
    rs = db_session.exec(select(RecoveryStatus)).first()
    assert rs.status.value == plan["recovery_status"]
    
    # Assert MesoRotation was backfilled
    rot = db_session.exec(select(MesoRotation)).first()
    assert rot.mesocycle_id == meso.id
    assert rot.id in plan["meso_rotations"]

def test_migrate_idempotency_guard(db_session):
    _setup_db(db_session, Phase.CUT)
    migrate(
        db_session, apply=True, current_mesocycle_ordinal=2, current_microcycle_ordinal=3, 
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )
    
    # Second apply should raise
    with pytest.raises(RuntimeError, match="Migration idempotency guard failed"):
        migrate(
            db_session, apply=True, current_mesocycle_ordinal=2, current_microcycle_ordinal=3, 
            mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 5) # different day to prove it guards anyway
        )

def test_shadow_validation_pass(db_session):
    _setup_db(db_session, Phase.CUT)
    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1, 
        mesocycle_length_weeks=4, seed_posture="PUSH", as_of=date(2026, 9, 4)
    )
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


def test_recovery_status_window_matches_readiness_pipeline_exactly(db_session):
    """Regression guard: caught live, right before applying to production.

    The pre-check window in _compute_recovery_status must match
    readiness.py's own BOOL_WINDOW_DAYS cutoff EXACTLY. Before the fix, the
    pre-check used an off-by-one-wider window (`<= BOOL_WINDOW_DAYS`, 11 days
    inclusive) than compute_sleep_ok/compute_subjective_ok's own internal
    window (10 days inclusive, via readiness.py's _trailing_rows). That let
    the pre-check see 5 readings (enough to "trust the real function") while
    the real function's own narrower window saw only 4 -- below its own
    BOOL_MIN_READINGS threshold -- and failed closed to False, producing a
    spurious POOR from data that was actually just borderline-sparse.

    as_of = 2026-09-04. Correct 10-day window (BOOL_WINDOW_DAYS=10) is
    [2026-08-26, 2026-09-04]. This fixture seeds 5 healthy readings, but one
    (2026-08-25) sits exactly one day OUTSIDE the correct window and inside
    only the old buggy window -- leaving exactly 4 valid readings in the
    correct window, one short of BOOL_MIN_READINGS=5.
    """
    db_session.add(EngineState(id=1, current_phase=Phase.STAB))
    for d in (
        date(2026, 8, 25),  # outside the correct window, inside the old buggy one
        date(2026, 8, 27),
        date(2026, 8, 29),
        date(2026, 8, 31),
        date(2026, 9, 2),
    ):
        db_session.add(DailyReadiness(date=d, sleep_ok=True, subjective_ok=True))
    db_session.add(TrainingSession(date=date(2026, 9, 2), day_role="Upper A", phase="STAB"))
    db_session.commit()

    plan = migrate(
        db_session, apply=False, current_mesocycle_ordinal=1, current_microcycle_ordinal=1,
        mesocycle_length_weeks=4, seed_posture="BUILD", as_of=date(2026, 9, 4)
    )

    # Correct behavior: only 4 valid readings fall in the true 10-day window
    # (2026-08-25 is excluded), below BOOL_MIN_READINGS -- insufficient data
    # defaults optimistically to NORMAL, not a spurious POOR.
    assert plan["recovery_status"] == "NORMAL", (
        "insufficient-data case must default to NORMAL, not fail closed to "
        "POOR via a pre-check/real-function window mismatch"
    )
