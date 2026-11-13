from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

from fogar_gateway import paths
from fogar_gateway.core.config import settings

_engine = None
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

# Decisión 5 de M14c: el gateway no crea nunca una BD en silencio. Lo activan `fogar-gw init`
# y `fogar-gw serve --create-db` (la imagen Docker).
CREATE_DB = False


class DatabaseMissing(RuntimeError):
    """No hay BD en la ruta configurada y no se ha pedido crearla."""


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


def _require_db() -> None:
    db = paths.sqlite_file(settings.DATABASE_URL)
    if db is not None and not db.exists() and not CREATE_DB:
        raise DatabaseMissing(
            f"No existe la base de datos {db}. Si es una instalación nueva, créala con "
            "`fogar-gw init`. Si ya tenías una, copia aquí su gateway.db (o usa "
            "FOGAR_GATEWAY_HOME/--home para apuntar a su directorio base)."
        )


def require_database() -> None:
    """Para el arranque, antes del candado: sin BD, se para sin crear nada (ni data/).

    Con un engine ya abierto (o inyectado en tests) la BD ya está elegida.
    """
    if _engine is None:
        _require_db()


def get_engine():
    global _engine
    if _engine is None:
        # Aquí y no solo en run_migrations: conectar crearía el fichero vacío, también desde
        # la CLI `alembic` (env.py usa este engine).
        _require_db()
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
    _require_db()  # antes de Alembic: el error sale tal cual, sin envolver
    command.upgrade(_alembic_config(), "head")


def get_db_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
