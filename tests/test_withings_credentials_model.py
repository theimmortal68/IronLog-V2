from datetime import datetime

from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models import EngineState, WithingsCredentials


EXPIRES_AT = datetime(2026, 7, 18, 12, 30, 0)
UPDATED_AT = datetime(2026, 7, 18, 12, 0, 0)


def test_withings_credentials_defaults_to_singleton_id_like_engine_state():
    assert WithingsCredentials(
        access_token="access",
        refresh_token="refresh",
        token_expires_at=EXPIRES_AT,
        updated_at=UPDATED_AT,
    ).id == EngineState().id == 1


def test_withings_credentials_round_trips_all_fields_with_null_last_synced_at():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        row = WithingsCredentials(
            access_token="access-token",
            refresh_token="refresh-token",
            token_expires_at=EXPIRES_AT,
            last_synced_at=None,
            updated_at=UPDATED_AT,
        )
        db.add(row)
        db.commit()

        saved = db.exec(select(WithingsCredentials)).one()

    assert saved.id == 1
    assert saved.access_token == "access-token"
    assert saved.refresh_token == "refresh-token"
    assert saved.token_expires_at == EXPIRES_AT
    assert saved.last_synced_at is None
    assert saved.updated_at == UPDATED_AT
