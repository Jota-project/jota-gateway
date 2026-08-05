import logging

from alembic import command
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
    assert {"id", "client_key", "tool_calls_enabled"} <= columns
    assert "system_prompt_extra" not in columns


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


def test_drop_system_prompt_extra_migration_applies_and_rolls_back(tmp_path, monkeypatch):
    """Revision 106077a95a0f drops `system_prompt_extra`; downgrading restores it
    with the same type/nullability as the initial migration (#100)."""
    db_path = tmp_path / "rollback.db"
    _point_at(monkeypatch, db_path)
    cfg = database._alembic_config()

    # Start from the initial schema — column present.
    command.upgrade(cfg, "21cb0cf4f6f9")
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"]: c for c in inspect(engine).get_columns("clients")}
    assert "system_prompt_extra" in columns
    assert columns["system_prompt_extra"]["nullable"] is True
    engine.dispose()

    # Upgrade to head (106077a95a0f) — column dropped.
    command.upgrade(cfg, "106077a95a0f")
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"] for c in inspect(engine).get_columns("clients")}
    assert "system_prompt_extra" not in columns
    engine.dispose()

    # Downgrade back to the initial revision — column restored.
    command.downgrade(cfg, "21cb0cf4f6f9")
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"]: c for c in inspect(engine).get_columns("clients")}
    assert "system_prompt_extra" in columns
    assert columns["system_prompt_extra"]["nullable"] is True
    engine.dispose()


def test_run_migrations_does_not_disable_existing_loggers(tmp_path, monkeypatch):
    """migrations/env.py calls fileConfig(alembic.ini) on every run_migrations()
    call — including at real gateway startup, not just in tests. alembic.ini's
    [loggers] section only declares root/sqlalchemy/alembic, so fileConfig's
    default disable_existing_loggers=True silently disables every already-
    created src.* logger (main.py imports all of them before lifespan() calls
    run_migrations()) for the rest of the process's life — no application log
    line, including the security/audit ones, ever reaches stdout again."""
    db_path = tmp_path / "logging_regression.db"
    _point_at(monkeypatch, db_path)

    probe_logger = logging.getLogger("src.some_module_that_already_exists")
    probe_logger.disabled = False

    database.run_migrations()

    assert probe_logger.disabled is False
