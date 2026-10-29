from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

from src.core.config import settings

_engine = None
_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _ensure_sqlite_dir(database_url: str) -> None:
    """Crea el directorio padre del fichero SQLite si no existe (issue #124).

    Idempotente. Docker lo enmascaraba con el bind mount de `./data`; en un
    checkout limpio, `sqlite:///data/gateway.db` fallaba con OperationalError.
    No-op para bases en memoria y para URLs que no son SQLite.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine
    if _engine is None:
        _ensure_sqlite_dir(settings.DATABASE_URL)
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
    cfg = Config(str(_ALEMBIC_INI))
    # Desde la app no se aplica la config de logging de alembic.ini (#249).
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations() -> None:
    """
    `alembic upgrade head` sobre settings.DATABASE_URL: crea el esquema en una BD nueva y
    aplica solo las pendientes en una ya versionada. Una BD con `clients` pero sin
    `alembic_version` (anterior a Alembic) ya no se sella: falla al crear la tabla (#265).
    """
    command.upgrade(_alembic_config(), "head")


def get_db_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
