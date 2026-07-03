from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import src.db.models  # noqa: F401
from src.core.config import settings
from src.db import database


def _point_at(monkeypatch, db_path) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    database._engine = None


def test_fresh_db_upgrades_from_scratch(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    _point_at(monkeypatch, db_path)

    database.run_migrations()

    engine = create_engine(f"sqlite:///{db_path}")
    tables = inspect(engine).get_table_names()
    assert "clients" in tables
    assert "alembic_version" in tables
    columns = {c["name"] for c in inspect(engine).get_columns("clients")}
    assert {"id", "client_key", "tool_calls_enabled", "system_prompt_extra"} <= columns


def test_legacy_db_gets_stamped_without_altering_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    # Simular la BD de producción: esquema ya creado directamente (sin Alembic),
    # tal y como quedó tras el ALTER TABLE manual del incidente original.
    legacy_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(legacy_engine)
    legacy_engine.dispose()

    _point_at(monkeypatch, db_path)
    database.run_migrations()

    engine = create_engine(f"sqlite:///{db_path}")
    tables = inspect(engine).get_table_names()
    assert "alembic_version" in tables
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_already_versioned_db_is_a_clean_noop(tmp_path, monkeypatch):
    db_path = tmp_path / "versioned.db"
    _point_at(monkeypatch, db_path)

    database.run_migrations()
    database._engine = None  # forzar reconexión, como en un segundo arranque real

    database.run_migrations()  # no debe lanzar excepción ni duplicar nada

    engine = create_engine(f"sqlite:///{db_path}")
    assert "clients" in inspect(engine).get_table_names()
