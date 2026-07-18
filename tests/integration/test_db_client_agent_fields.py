"""Verify that db_client.get_session() propagates default_agent and
allowed_agents (parsed from the JSON-stored field) into ClientConfig.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from src.db import database as db_database
from src.db.models import ClientRecord
from src.services.db_client import db_client


@pytest.fixture(autouse=True)
def db_engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_database, "_engine", engine)
    db_client.invalidate("any")  # safety: clear stale cache
    yield engine
    db_client.invalidate("any")


async def _insert(engine, key, default_agent=None, allowed_agents=None):
    from sqlmodel import Session
    with Session(engine) as s:
        s.add(ClientRecord(
            name="t", client_key=key, is_active=True,
            default_agent=default_agent, allowed_agents=allowed_agents,
        ))
        s.commit()


async def test_default_agent_propagated(db_engine):
    await _insert(db_engine, "k1", default_agent="x")
    db_client.invalidate("k1")
    _, config = await db_client.get_session("k1")
    assert config.default_agent == "x"


async def test_allowed_agents_list_propagated(db_engine):
    await _insert(db_engine, "k2", allowed_agents='["a","b"]')
    db_client.invalidate("k2")
    _, config = await db_client.get_session("k2")
    assert config.allowed_agents == ["a", "b"]


async def test_allowed_agents_none_means_no_restriction(db_engine):
    await _insert(db_engine, "k3", allowed_agents=None)
    db_client.invalidate("k3")
    _, config = await db_client.get_session("k3")
    assert config.allowed_agents is None


async def test_allowed_agents_empty_list_means_deny_all(db_engine):
    await _insert(db_engine, "k4", allowed_agents="[]")
    db_client.invalidate("k4")
    _, config = await db_client.get_session("k4")
    assert config.allowed_agents == []


async def test_allowed_agents_malformed_json_raises_value_error(db_engine):
    await _insert(db_engine, "k5", allowed_agents='["malformed')
    db_client.invalidate("k5")
    with pytest.raises(ValueError):
        await db_client.get_session("k5")
