from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlmodel import Session, create_engine

from src.core.config import settings

_engine = None
_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.DATABASE_URL,
            connect_args={"check_same_thread": False},
        )
    return _engine


def dispose_engine() -> None:
    """Disposes the current engine's connection pool and resets the module-
    level singleton so a subsequent get_engine() call builds a fresh one.
    Called by the app lifespan on shutdown (issue #110) — without this, the
    SQLite file handle can outlive the process's graceful-shutdown window,
    which is how "database is locked" errors on restart happen."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def _alembic_config() -> Config:
    return Config(str(_ALEMBIC_INI))


def run_migrations() -> None:
    """
    Aplica el esquema de `clients` vía Alembic sobre settings.DATABASE_URL.

    - BD nueva (sin tabla `clients` ni `alembic_version`): upgrade head desde cero.
    - BD legacy (`clients` ya existe pero `alembic_version` no — esquema ya al
      día tras un ALTER TABLE manual, como la producción tras el incidente
      original): stamp head, sin ejecutar ningún DDL.
    - BD ya versionada: upgrade head aplica solo las migraciones pendientes.
    """
    tables = inspect(get_engine()).get_table_names()
    cfg = _alembic_config()
    if "clients" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")


def get_db_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
