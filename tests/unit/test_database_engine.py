import pytest

from src.core.config import settings
from src.db import database


@pytest.fixture
def fresh_engine(monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    yield
    database.dispose_engine()


def test_get_engine_creates_missing_sqlite_parent_dir(monkeypatch, tmp_path, fresh_engine):
    db_file = tmp_path / "nested" / "data" / "gateway.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_file}")

    engine = database.get_engine()

    assert db_file.parent.is_dir()
    with engine.connect():
        pass  # antes: OperationalError "unable to open database file"
    assert db_file.exists()


def test_get_engine_is_idempotent_when_parent_dir_exists(monkeypatch, tmp_path, fresh_engine):
    db_file = tmp_path / "gateway.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_file}")

    database.get_engine()
    monkeypatch.setattr(database, "_engine", None)
    database.get_engine()  # no debe fallar con el directorio ya creado


def test_get_engine_ignores_in_memory_sqlite(monkeypatch, fresh_engine):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///:memory:")
    database.get_engine()  # no debe intentar crear ningún directorio
