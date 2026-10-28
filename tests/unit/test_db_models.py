import json

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.db.models import ClientRecord


def _mem_engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


def test_client_key_unique():
    engine = _mem_engine()
    with pytest.raises(IntegrityError), Session(engine) as s:
        s.add(ClientRecord(name="A", client_key="dup"))
        s.add(ClientRecord(name="B", client_key="dup"))
        s.commit()


def test_allowed_agents_json():
    engine = _mem_engine()
    with Session(engine) as s:
        s.add(
            ClientRecord(name="X", client_key="k2", allowed_agents=json.dumps(["main", "helper"]))
        )
        s.commit()
        rec = s.exec(select(ClientRecord)).first()
    assert json.loads(rec.allowed_agents) == ["main", "helper"]
