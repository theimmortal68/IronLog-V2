from datetime import date, datetime

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models import MissedDayRecord, Program, ProgramDay


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_program_day(db: Session) -> int:
    program = Program(id=1, name="Phase 1", phase="CUT", duration_weeks=8)
    db.add(program)
    db.add(ProgramDay(
        id=1,
        program_id=program.id,
        day_index=1,
        day_role="D1 Upper Push",
        is_rest=False,
    ))
    db.commit()
    return 1


def test_missed_day_record_round_trips_all_fields_with_null_resolved_at():
    engine = _engine()
    detected_at = datetime(2026, 7, 20, 6, 30, 0)

    with Session(engine) as db:
        program_day_id = _seed_program_day(db)
        db.add(MissedDayRecord(
            program_day_id=program_day_id,
            week_start_date=date(2026, 7, 13),
            detected_at=detected_at,
            status="PENDING",
            resolved_at=None,
        ))
        db.commit()

        saved = db.exec(select(MissedDayRecord)).one()

    assert saved.id is not None
    assert saved.program_day_id == 1
    assert saved.week_start_date == date(2026, 7, 13)
    assert saved.detected_at == detected_at
    assert saved.status == "PENDING"
    assert saved.resolved_at is None


def test_missed_day_record_allows_multiple_rows_for_same_program_day():
    engine = _engine()

    with Session(engine) as db:
        program_day_id = _seed_program_day(db)
        db.add_all([
            MissedDayRecord(
                program_day_id=program_day_id,
                week_start_date=date(2026, 7, 13),
                detected_at=datetime(2026, 7, 20, 6, 30, 0),
            ),
            MissedDayRecord(
                program_day_id=program_day_id,
                week_start_date=date(2026, 7, 20),
                detected_at=datetime(2026, 7, 27, 6, 30, 0),
                status="RESCHEDULED",
            ),
        ])
        db.commit()

        saved = db.exec(
            select(MissedDayRecord)
            .where(MissedDayRecord.program_day_id == program_day_id)
            .order_by(MissedDayRecord.week_start_date)
        ).all()

    assert len(saved) == 2
    assert {row.program_day_id for row in saved} == {1}
    assert saved[0].id is not None
    assert saved[1].id is not None
    assert saved[0].id != saved[1].id
    assert saved[0].week_start_date == date(2026, 7, 13)
    assert saved[1].week_start_date == date(2026, 7, 20)
    assert saved[0].status == "PENDING"
    assert saved[1].status == "RESCHEDULED"
