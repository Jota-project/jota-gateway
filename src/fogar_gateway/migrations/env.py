from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

import fogar_gateway.db.models  # noqa: F401 - registra ClientRecord en SQLModel.metadata
from fogar_gateway.core.config import settings
from fogar_gateway.db.database import get_engine

config = context.config
# `%` escapado: ConfigParser interpola, y la ruta va codificada (%23, %3F…; ver paths.py).
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

# La app llama a run_migrations() con cfg.attributes["configure_logger"] = False:
# el [logger_root] level = WARNING de alembic.ini dejaría el root de la app en
# WARNING y ningún INFO de fogar_gateway.* saldría en producción (#249). La CLI `alembic`
# no pone el atributo, así que conserva la config de logging de alembic.ini.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    # disable_existing_loggers=False (#156): the default (True) disables every
    # already-created logger not listed in alembic.ini's [loggers] section
    # (root/sqlalchemy/alembic) — including every fogar_gateway.* module logger, since
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
        if connection.dialect.name == "sqlite":
            # Las conexiones abren con foreign_keys=ON (db/database.py); una migración por lotes
            # recrea tablas (copia, borra, renombra) y, con ellas activas, borrar `clients`
            # borraría en cascada sus conversaciones. Fuera de transacción, o SQLite lo ignora.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
