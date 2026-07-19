from datetime import datetime

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models import EngineState, GoalSettings


UPDATED_AT = datetime(2026, 7, 18, 12, 0, 0)


def test_goal_settings_defaults_to_singleton_id_like_engine_state():
    assert GoalSettings(
        target_bodyweight=213.0,
        target_bodyweight_tolerance=2.0,
        updated_at=UPDATED_AT,
    ).id == EngineState().id == 1


def test_goal_settings_round_trips_all_fields_with_null_body_fat_fields():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        row = GoalSettings(
            target_bodyweight=213.0,
            target_bodyweight_tolerance=2.0,
            target_body_fat_pct=None,
            target_body_fat_pct_tolerance=None,
            updated_at=UPDATED_AT,
        )
        db.add(row)
        db.commit()

        saved = db.exec(select(GoalSettings)).one()

    assert saved.id == 1
    assert saved.target_bodyweight == 213.0
    assert saved.target_bodyweight_tolerance == 2.0
    assert saved.target_body_fat_pct is None
    assert saved.target_body_fat_pct_tolerance is None
    assert saved.updated_at == UPDATED_AT
