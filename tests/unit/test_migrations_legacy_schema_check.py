"""G-42 (#218): el modo «legacy stamp» no sella `head` sobre un esquema incompleto."""

import pytest
from sqlalchemy import create_engine, inspect, text

from src.core.config import settings
from src.db import database


def _legacy_db(tmp_path, monkeypatch, columns_sql: str):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE clients ({columns_sql})"))
    engine.dispose()
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    database._engine = None
    return db_path


def test_legacy_stamp_fails_listing_missing_columns_without_stamping(tmp_path, monkeypatch):
    # Faltan casi todas las columnas del modelo (p. ej. una BD anterior a varias migraciones).
    db_path = _legacy_db(
        tmp_path, monkeypatch, "id VARCHAR PRIMARY KEY, name VARCHAR, client_key VARCHAR"
    )

    with pytest.raises(RuntimeError) as exc:
        database.run_migrations()

    msg = str(exc.value)
    assert "tool_calls_enabled" in msg and "push_enabled" in msg
    assert "is_active" in msg
    assert "client_key" not in msg.split(":", 1)[1]  # solo lista las AUSENTES

    # Ni DDL ni sello.
    engine = create_engine(f"sqlite:///{db_path}")
    assert "alembic_version" not in inspect(engine).get_table_names()
    assert {c["name"] for c in inspect(engine).get_columns("clients")} == {
        "id",
        "name",
        "client_key",
    }
    engine.dispose()
    database.dispose_engine()


def test_legacy_stamp_with_complete_schema_still_stamps(tmp_path, monkeypatch):
    from sqlmodel import SQLModel

    import src.db.models  # noqa: F401

    db_path = tmp_path / "ok.db"
    SQLModel.metadata.create_all(create_engine(f"sqlite:///{db_path}"))
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    database._engine = None

    database.run_migrations()

    engine = create_engine(f"sqlite:///{db_path}")
    assert "alembic_version" in inspect(engine).get_table_names()
    engine.dispose()
    database.dispose_engine()
