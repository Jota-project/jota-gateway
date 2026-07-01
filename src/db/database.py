from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from src.core.config import settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.DATABASE_URL,
            connect_args={"check_same_thread": False},
        )
    return _engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_db_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
