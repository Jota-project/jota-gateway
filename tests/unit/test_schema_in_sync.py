from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import src.db.models  # noqa: F401 - registra ClientRecord en SQLModel.metadata
from src.core.config import settings
from src.db import database


def test_head_migrations_match_current_model(tmp_path, monkeypatch):
    """
    Si este test falla, alguien cambió ClientRecord sin generar la migración
    correspondiente (`alembic revision --autogenerate`). Es el escenario
    exacto del issue #73: una columna nueva en el modelo que nunca llega
    al esquema real.
    """
    db_path = tmp_path / "sync_check.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    database._engine = None

    database.run_migrations()

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        diff = compare_metadata(migration_ctx, SQLModel.metadata)

    assert diff == [], f"Modelo y migraciones desincronizados: {diff}"
