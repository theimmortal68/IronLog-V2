"""Database engine + helpers. SQLite now; change the URL for Postgres later."""
from sqlmodel import SQLModel, Session, create_engine

# import models so SQLModel.metadata knows every table before create_all
from . import models  # noqa: F401

DATABASE_URL = "sqlite:///ironlog.db"
engine = create_engine(DATABASE_URL, echo=False)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
