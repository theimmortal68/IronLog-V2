from datetime import date

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models import CardioLog


def test_cardio_log_round_trips_all_fields_with_nullable_values():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        row = CardioLog(
            date=date(2026, 7, 21),
            duration_minutes=45,
            avg_hr=None,
            modality="WALK",
            incline_pct=None,
            backward_walk_done=True,
        )
        db.add(row)
        db.commit()

        saved = db.exec(select(CardioLog)).one()

    assert saved.id is not None
    assert saved.date == date(2026, 7, 21)
    assert saved.duration_minutes == 45
    assert saved.avg_hr is None
    assert saved.modality == "WALK"
    assert saved.incline_pct is None
    assert saved.backward_walk_done is True
    assert saved.created_at is not None


def test_cardio_log_allows_multiple_rows_for_same_date():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    session_date = date(2026, 7, 21)
    with Session(engine) as db:
        db.add_all([
            CardioLog(
                date=session_date,
                duration_minutes=30,
                avg_hr=122,
                modality="WALK",
            ),
            CardioLog(
                date=session_date,
                duration_minutes=40,
                avg_hr=130,
                modality="TREADMILL",
                incline_pct=7.5,
                backward_walk_done=True,
            ),
        ])
        db.commit()

        saved = db.exec(
            select(CardioLog)
            .where(CardioLog.date == session_date)
            .order_by(CardioLog.id)
        ).all()

    assert len(saved) == 2
    assert {row.date for row in saved} == {session_date}
    assert saved[0].id is not None
    assert saved[1].id is not None
    assert saved[0].id != saved[1].id
    assert saved[0].modality == "WALK"
    assert saved[0].incline_pct is None
    assert saved[0].backward_walk_done is False
    assert saved[1].modality == "TREADMILL"
    assert saved[1].incline_pct == 7.5
    assert saved[1].backward_walk_done is True
