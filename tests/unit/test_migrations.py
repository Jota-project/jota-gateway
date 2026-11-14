import logging

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

import fogar_gateway.db.models  # noqa: F401
from fogar_gateway.core.config import settings
from fogar_gateway.db import database


def _point_at(monkeypatch, db_path) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "CREATE_DB", True)
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
    created fogar_gateway.* logger (main.py imports all of them before lifespan() calls
    run_migrations()) for the rest of the process's life — no application log
    line, including the security/audit ones, ever reaches stdout again."""
    db_path = tmp_path / "logging_regression.db"
    _point_at(monkeypatch, db_path)

    probe_logger = logging.getLogger("fogar_gateway.some_module_that_already_exists")
    probe_logger.disabled = False

    database.run_migrations()

    assert probe_logger.disabled is False


def test_dispose_engine_disposes_and_resets_to_none(tmp_path, monkeypatch):
    db_path = tmp_path / "dispose.db"
    _point_at(monkeypatch, db_path)

    engine = database.get_engine()
    assert database._engine is engine

    database.dispose_engine()

    assert database._engine is None


def test_dispose_engine_is_a_noop_when_never_initialized(monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    database.dispose_engine()  # must not raise
    assert database._engine is None


def test_conversations_migration_applies_and_rolls_back(tmp_path, monkeypatch):
    """Revision 5b1e7c2d9a40 (M19) adds `conversations` with its unique indexes and keeps
    the clients; downgrading drops the table and leaves `clients` alone."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    db_path = tmp_path / "conversations.db"
    _point_at(monkeypatch, db_path)
    cfg = database._alembic_config()

    command.upgrade(cfg, "106077a95a0f")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO clients (id, name, client_key, is_active, created_at, stt_language, "
            "stt_vad_thold, tts_voice, tts_speed, barge_in_enabled, barge_in_min_chars, "
            "silence_timeout_s, max_silence_turns, push_enabled, tool_calls_enabled) VALUES "
            "('tv', 'TV', 'k', 1, '2026-11-14', 'es', 0, 'v', 1, 1, 5, 2, 3, 1, 0)"
        ))
    assert "conversations" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"]: c for c in inspect(engine).get_columns("conversations")}
    assert set(columns) == {
        "id", "client_id", "agent", "session_key", "kind", "day", "created_at",
        "last_turn_at", "turn_count",
    }
    assert columns["day"]["nullable"] is True and columns["session_key"]["nullable"] is False
    unique = {i["name"] for i in inspect(engine).get_indexes("conversations") if i["unique"]}
    assert unique == {"ix_conversations_session_key", "uq_conversations_default_day"}
    insert = text(
        "INSERT INTO conversations (id, client_id, agent, session_key, kind, day, created_at, "
        "turn_count) VALUES (:id, 'tv', 'main', :sk, 'default', '2026-11-14', '2026-11-14', 0)"
    )
    with engine.begin() as c:
        c.execute(insert, {"id": "c1", "sk": "agent:main:fogar-c1"})
    with pytest.raises(IntegrityError), engine.begin() as c:  # una por cliente, agente y día
        c.execute(insert, {"id": "c2", "sk": "agent:main:fogar-c2"})
    engine.dispose()

    command.downgrade(cfg, "106077a95a0f")
    engine = create_engine(f"sqlite:///{db_path}")
    assert "conversations" not in inspect(engine).get_table_names()
    with engine.connect() as c:
        assert c.execute(text("SELECT id FROM clients")).scalars().all() == ["tv"]
    engine.dispose()
