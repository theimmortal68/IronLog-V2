from datetime import date, datetime

import pytest
from sqlmodel import SQLModel, Session as DbSession, create_engine, select

from ironlog.models.library import EngineState
from ironlog.models.program import MissedDayRecord, Program, ProgramDay
from ironlog.models.session import Session as WorkoutSession
from ironlog.persistence.missed_days import check_missed_days, resolve_day_date


@pytest.fixture(name="db")
def db_fixture():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with DbSession(engine) as session:
        yield session


def _seed_active_program(db: DbSession, day_specs):
    db.add(Program(id=1, name="Phase 1", phase="CUT", duration_weeks=8))
    db.add(EngineState(id=1, active_program_id=1))
    day_ids = {}
    for program_day_id, day_index, day_role, is_rest in day_specs:
        db.add(ProgramDay(
            id=program_day_id,
            program_id=1,
            day_index=day_index,
            day_role=day_role,
            is_rest=is_rest,
        ))
        day_ids[day_role] = program_day_id
    db.commit()
    return day_ids


def test_resolve_day_date_returns_monday_and_sunday():
    week_start = date(2026, 7, 13)

    assert resolve_day_date(1, week_start) == week_start
    assert resolve_day_date(7, week_start) == date(2026, 7, 19)


def test_check_missed_days_creates_pending_record_for_eligible_missed_day(db):
    day_ids = _seed_active_program(db, [
        (1, 1, "D1 Upper Push", False),
    ])

    summary = check_missed_days(db, as_of=datetime(2026, 7, 14, 6, 0, 0))

    assert summary == {"newly_missed": 1, "resolved": 0}
    records = db.exec(select(MissedDayRecord)).all()
    assert len(records) == 1
    assert records[0].program_day_id == day_ids["D1 Upper Push"]
    assert records[0].week_start_date == date(2026, 7, 13)
    assert records[0].status == "PENDING"
    assert records[0].resolved_at is None


def test_check_missed_days_does_not_duplicate_same_missed_day(db):
    _seed_active_program(db, [
        (1, 1, "D1 Upper Push", False),
    ])
    as_of = datetime(2026, 7, 14, 6, 0, 0)

    first_summary = check_missed_days(db, as_of=as_of)
    second_summary = check_missed_days(db, as_of=as_of)

    assert first_summary == {"newly_missed": 1, "resolved": 0}
    assert second_summary == {"newly_missed": 0, "resolved": 0}
    records = db.exec(select(MissedDayRecord)).all()
    assert len(records) == 1


def test_check_missed_days_waits_until_grace_window_has_passed(db):
    _seed_active_program(db, [
        (1, 1, "D1 Upper Push", False),
    ])

    summary = check_missed_days(db, as_of=datetime(2026, 7, 14, 3, 0, 0))

    assert summary == {"newly_missed": 0, "resolved": 0}
    assert db.exec(select(MissedDayRecord)).all() == []


def test_check_missed_days_resolves_pending_record_with_matching_session(db):
    day_ids = _seed_active_program(db, [
        (1, 1, "D1 Upper Push", False),
    ])
    db.add(MissedDayRecord(
        program_day_id=day_ids["D1 Upper Push"],
        week_start_date=date(2026, 7, 13),
        status="PENDING",
    ))
    db.add(WorkoutSession(
        date=date(2026, 7, 16),
        day_role="D1 Upper Push",
        phase="CUT",
    ))
    db.commit()
    as_of = datetime(2026, 7, 18, 12, 0, 0)

    summary = check_missed_days(db, as_of=as_of)

    assert summary == {"newly_missed": 0, "resolved": 1}
    record = db.exec(select(MissedDayRecord)).one()
    assert record.status == "RESOLVED"
    assert record.resolved_at == as_of


def test_check_missed_days_resolves_acknowledged_record_with_matching_session(db):
    day_ids = _seed_active_program(db, [
        (1, 1, "D1 Upper Push", False),
    ])
    db.add(MissedDayRecord(
        program_day_id=day_ids["D1 Upper Push"],
        week_start_date=date(2026, 7, 13),
        status="ACKNOWLEDGED",
    ))
    db.add(WorkoutSession(
        date=date(2026, 7, 16),
        day_role="D1 Upper Push",
        phase="CUT",
    ))
    db.commit()
    as_of = datetime(2026, 7, 18, 12, 0, 0)

    summary = check_missed_days(db, as_of=as_of)

    assert summary == {"newly_missed": 0, "resolved": 1}
    record = db.exec(select(MissedDayRecord)).one()
    assert record.status == "RESOLVED"
    assert record.resolved_at == as_of


@pytest.mark.parametrize("engine_state_exists", [False, True])
def test_check_missed_days_no_active_program_is_noop(db, engine_state_exists):
    db.add(Program(id=1, name="Phase 1", phase="CUT", duration_weeks=8))
    db.add(ProgramDay(
        id=1,
        program_id=1,
        day_index=1,
        day_role="D1 Upper Push",
        is_rest=False,
    ))
    db.add(MissedDayRecord(
        program_day_id=1,
        week_start_date=date(2026, 7, 13),
        status="PENDING",
    ))
    db.add(WorkoutSession(
        date=date(2026, 7, 16),
        day_role="D1 Upper Push",
        phase="CUT",
    ))
    if engine_state_exists:
        db.add(EngineState(id=1, active_program_id=None))
    db.commit()

    summary = check_missed_days(db, as_of=datetime(2026, 7, 18, 12, 0, 0))

    assert summary == {"newly_missed": 0, "resolved": 0}
    record = db.exec(select(MissedDayRecord)).one()
    assert record.status == "PENDING"
    assert record.resolved_at is None


def test_check_missed_days_never_flags_rest_day(db):
    _seed_active_program(db, [
        (1, 1, "", True),
    ])

    summary = check_missed_days(db, as_of=datetime(2026, 7, 14, 6, 0, 0))

    assert summary == {"newly_missed": 0, "resolved": 0}
    assert db.exec(select(MissedDayRecord)).all() == []


def test_check_missed_days_only_flags_untrained_days_in_partial_week(db):
    _seed_active_program(db, [
        (1, 1, "D1 Upper Push", False),
        (2, 2, "D2 Lower A", False),
    ])
    db.add(WorkoutSession(
        date=date(2026, 7, 13),
        day_role="D1 Upper Push",
        phase="CUT",
    ))
    db.commit()

    summary = check_missed_days(db, as_of=datetime(2026, 7, 15, 6, 0, 0))

    assert summary == {"newly_missed": 1, "resolved": 0}
    records = db.exec(select(MissedDayRecord)).all()
    assert len(records) == 1
    assert records[0].program_day_id == 2
    assert records[0].week_start_date == date(2026, 7, 13)
