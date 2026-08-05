from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

import src.db.models  # noqa: F401 - registra ClientRecord en SQLModel.metadata
from src.core.config import settings
from src.db.database import get_engine

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    # disable_existing_loggers=False: the default (True) disables every
    # already-created logger not listed in alembic.ini's [loggers] section
    # (root/sqlalchemy/alembic) — including every src.* module logger, since
    # main.py imports them all before lifespan() calls run_migrations(). That
    # silently kills all application logging, in production, on every startup.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reutiliza get_engine() en vez de construir un engine propio desde la URL:
    # así respeta cualquier engine inyectado en tests (p.ej. el SQLite en
    # memoria monkeypatcheado por la fixture `db_engine` de integración).
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
