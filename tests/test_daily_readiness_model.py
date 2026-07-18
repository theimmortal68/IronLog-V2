from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models import DailyReadiness


def test_daily_readiness_round_trips_nullable_values_and_sources():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        row = DailyReadiness(
            date=date(2026, 7, 18),
            bodyweight=202.5,
            resting_hr=54.0,
            resting_hr_source="polar",
            sleep_ok=True,
            subjective_ok=None,
        )
        db.add(row)
        db.commit()

        saved = db.exec(select(DailyReadiness)).one()

    assert saved.id is not None
    assert saved.date == date(2026, 7, 18)
    assert saved.bodyweight == 202.5
    assert saved.bodyweight_source == "manual"
    assert saved.resting_hr == 54.0
    assert saved.resting_hr_source == "polar"
    assert saved.sleep_ok is True
    assert saved.subjective_ok is None
    assert saved.created_at is not None


def test_daily_readiness_date_is_unique():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(DailyReadiness(date=date(2026, 7, 18), bodyweight=202.5))
        db.commit()

        db.add(DailyReadiness(date=date(2026, 7, 18), bodyweight=203.0))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        rows = db.exec(select(DailyReadiness)).all()

    assert len(rows) == 1
    assert rows[0].bodyweight == 202.5
