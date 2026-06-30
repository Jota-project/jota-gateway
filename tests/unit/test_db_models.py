import json
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select
from src.db.models import ClientRecord


def _mem_engine():
    e = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    return e


def test_defaults():
    engine = _mem_engine()
    with Session(engine) as s:
        s.add(ClientRecord(name="ESP32", client_key="k1"))
        s.commit()
        rec = s.exec(select(ClientRecord)).first()
    assert rec.id is not None
    assert rec.is_active is True
    assert rec.stt_language == "es"
    assert rec.tts_voice == "af_heart"
    assert rec.barge_in_enabled is True
    assert rec.barge_in_min_chars == 5
    assert rec.tts_speed == 1.0
    assert rec.silence_timeout_s == 2.0


def test_client_key_unique():
    engine = _mem_engine()
    with pytest.raises(IntegrityError):
        with Session(engine) as s:
            s.add(ClientRecord(name="A", client_key="dup"))
            s.add(ClientRecord(name="B", client_key="dup"))
            s.commit()


def test_allowed_agents_json():
    engine = _mem_engine()
    with Session(engine) as s:
        s.add(ClientRecord(name="X", client_key="k2",
                           allowed_agents=json.dumps(["main", "helper"])))
        s.commit()
        rec = s.exec(select(ClientRecord)).first()
    assert json.loads(rec.allowed_agents) == ["main", "helper"]
