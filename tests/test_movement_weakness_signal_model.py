from datetime import date, datetime, timedelta

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models import Movement, MovementWeaknessSignal
from ironlog.models.session import Session as IronSession


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_movement_and_sessions(db: Session) -> int:
    movement = Movement(id=1, name="Bench Press [PB]", base_name="Bench Press")
    db.add(movement)
    db.add(IronSession(id=1, date=date(2026, 7, 18), day_role="D1 Upper Push", phase="CUT"))
    db.add(IronSession(id=2, date=date(2026, 7, 19), day_role="D2 Lower A", phase="CUT"))
    db.commit()
    return movement.id


def test_movement_weakness_signal_round_trips_all_fields_with_nullable_growth_rate():
    engine = _engine()
    computed_at = datetime(2026, 7, 19, 12, 0, 0)

    with Session(engine) as db:
        movement_id = _seed_movement_and_sessions(db)
        db.add(MovementWeaknessSignal(
            movement_id=movement_id,
            session_id=1,
            computed_at=computed_at,
            stalled=True,
            growth_rate=None,
            lagging=False,
            is_weak=True,
        ))
        db.commit()

        saved = db.exec(select(MovementWeaknessSignal)).one()

    assert saved.id is not None
    assert saved.movement_id == 1
    assert saved.session_id == 1
    assert saved.computed_at == computed_at
    assert saved.stalled is True
    assert saved.growth_rate is None
    assert saved.lagging is False
    assert saved.is_weak is True


def test_movement_weakness_signal_allows_multiple_rows_for_same_movement():
    engine = _engine()
    first_at = datetime(2026, 7, 19, 12, 0, 0)
    second_at = first_at + timedelta(hours=1)

    with Session(engine) as db:
        movement_id = _seed_movement_and_sessions(db)
        db.add_all([
            MovementWeaknessSignal(
                movement_id=movement_id,
                session_id=1,
                computed_at=first_at,
                stalled=True,
                growth_rate=None,
                lagging=True,
                is_weak=True,
            ),
            MovementWeaknessSignal(
                movement_id=movement_id,
                session_id=2,
                computed_at=second_at,
                stalled=False,
                growth_rate=0.12,
                lagging=False,
                is_weak=False,
            ),
        ])
        db.commit()

        saved = db.exec(
            select(MovementWeaknessSignal)
            .where(MovementWeaknessSignal.movement_id == movement_id)
            .order_by(MovementWeaknessSignal.computed_at)
        ).all()

    assert len(saved) == 2
    assert {row.movement_id for row in saved} == {1}
    assert saved[0].id is not None
    assert saved[1].id is not None
    assert saved[0].id != saved[1].id
    assert saved[0].computed_at == first_at
    assert saved[1].computed_at == second_at
    assert saved[1].growth_rate == 0.12
