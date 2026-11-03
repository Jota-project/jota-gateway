"""Decisión 5 de M14c: el gateway no crea nunca una BD en silencio (#295)."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, inspect

from jota_gateway import paths
from jota_gateway.core.config import Settings, settings
from jota_gateway.db import database


@pytest.fixture
def home(tmp_path, monkeypatch):
    base = tmp_path / "base"
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(base))
    monkeypatch.setattr(settings, "DATABASE_URL", Settings(_env_file=None).DATABASE_URL)
    monkeypatch.setattr(database, "_engine", None)
    yield base
    database.dispose_engine()


def test_missing_db_is_refused_and_nothing_is_created(home):
    with pytest.raises(database.DatabaseMissing, match="jota-gw init"):
        database.run_migrations()
    assert not (home / "data" / "gateway.db").exists()


def test_create_db_creates_and_migrates(home, monkeypatch):
    monkeypatch.setattr(database, "CREATE_DB", True)
    database.run_migrations()
    db = home / "data" / "gateway.db"
    assert "alembic_version" in inspect(create_engine(f"sqlite:///{db}")).get_table_names()


def test_existing_db_is_migrated_without_create_db(home, monkeypatch):
    monkeypatch.setattr(database, "CREATE_DB", True)
    database.run_migrations()
    database.dispose_engine()
    monkeypatch.setattr(database, "CREATE_DB", False)
    database.run_migrations()  # ya existe: no hace falta permiso


def test_home_with_uri_characters_works_end_to_end(tmp_path, monkeypatch):
    base = tmp_path / "a#b?c%d e"
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(base))
    monkeypatch.setattr(settings, "DATABASE_URL", Settings(_env_file=None).DATABASE_URL)
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "CREATE_DB", True)
    try:
        database.run_migrations()
    finally:
        database.dispose_engine()
    assert paths.sqlite_file(settings.DATABASE_URL) == base / "data" / "gateway.db"
    assert (base / "data" / "gateway.db").is_file()


def test_memory_db_needs_no_permission(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "_engine", None)
    try:
        database.run_migrations()
    finally:
        database.dispose_engine()


def test_get_engine_refuses_too(home):
    """`alembic` y cualquier otro uso pasan por get_engine(): conectar crearía el fichero."""
    with pytest.raises(database.DatabaseMissing):
        database.get_engine()
    assert not (home / "data").exists()


def test_require_database_skips_an_already_open_engine(home, monkeypatch):
    monkeypatch.setattr(database, "_engine", MagicMock())  # p. ej. el inyectado en integración
    database.require_database()
