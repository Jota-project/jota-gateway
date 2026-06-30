import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from src.core.exceptions import ClientInactive, ClientNotFound
from src.db.models import ClientRecord
from src.services.db_client import DbClient


def _engine_with(*records):
    e = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        for r in records:
            s.add(r)
        s.commit()
    return e


@pytest.mark.asyncio
async def test_get_session_returns_client_and_config():
    engine = _engine_with(ClientRecord(name="ESP32 salón", client_key="valid-key"))
    client = DbClient(engine=engine)
    c, cfg = await client.get_session("valid-key")
    assert c.client_key == "valid-key"
    assert c.name == "ESP32 salón"
    assert c.is_active is True
    assert cfg.stt_language == "es"
    assert cfg.tts_voice == "af_heart"
    assert cfg.barge_in_enabled is True


@pytest.mark.asyncio
async def test_get_session_raises_client_not_found():
    engine = _engine_with(ClientRecord(name="A", client_key="real-key"))
    client = DbClient(engine=engine)
    with pytest.raises(ClientNotFound):
        await client.get_session("nonexistent")


@pytest.mark.asyncio
async def test_get_session_raises_client_inactive():
    engine = _engine_with(ClientRecord(name="Off", client_key="off-key", is_active=False))
    client = DbClient(engine=engine)
    with pytest.raises(ClientInactive):
        await client.get_session("off-key")


@pytest.mark.asyncio
async def test_get_session_cached():
    """Segunda llamada debe venir del caché sin tocar la BD."""
    engine = _engine_with(ClientRecord(name="Cache", client_key="c-key"))
    client = DbClient(engine=engine)
    r1 = await client.get_session("c-key")
    # Modificar la BD directamente
    with Session(engine) as s:
        rec = s.exec(select(ClientRecord)).first()
        rec.name = "Changed"
        s.add(rec)
        s.commit()
    r2 = await client.get_session("c-key")
    assert r2 is r1  # mismo objeto del caché


@pytest.mark.asyncio
async def test_invalidate_clears_cache():
    engine = _engine_with(ClientRecord(name="Before", client_key="inv-key"))
    client = DbClient(engine=engine)
    await client.get_session("inv-key")
    # Modificar BD y limpiar caché
    with Session(engine) as s:
        rec = s.exec(select(ClientRecord)).first()
        rec.name = "After"
        s.add(rec)
        s.commit()
    client.invalidate("inv-key")
    c2, _ = await client.get_session("inv-key")
    assert c2.name == "After"
