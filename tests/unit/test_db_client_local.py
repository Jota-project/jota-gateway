import asyncio
import threading

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.core.exceptions import ClientInactive, ClientNotFound
from src.db.models import ClientRecord
from src.services.db_client import DbClient


def _engine_with(*records):
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
async def test_get_session_reads_the_db_every_call():
    """#265: sin caché, un cambio hecho directamente en la BD se ve en la siguiente llamada."""
    engine = _engine_with(ClientRecord(name="Before", client_key="c-key"))
    client = DbClient(engine=engine)
    await client.get_session("c-key")
    with Session(engine) as s:
        rec = s.exec(select(ClientRecord)).first()
        rec.is_active = False
        s.add(rec)
        s.commit()
    with pytest.raises(ClientInactive):
        await client.get_session("c-key")


@pytest.mark.asyncio
async def test_slow_query_does_not_block_the_event_loop(monkeypatch):
    """#240: la consulta corre en un hilo; el loop sigue atendiendo otras corrutinas."""
    engine = _engine_with(ClientRecord(name="Slow", client_key="slow-key"))
    client = DbClient(engine=engine)
    started, release = threading.Event(), threading.Event()
    original_exec = Session.exec

    def slow_exec(self, *args, **kwargs):
        started.set()
        release.wait(timeout=5)
        return original_exec(self, *args, **kwargs)

    monkeypatch.setattr(Session, "exec", slow_exec)
    pending = asyncio.create_task(client.get_session("slow-key"))
    try:
        assert await asyncio.to_thread(started.wait, 5), "la consulta nunca arrancó"
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=1)  # el loop responde
        assert not pending.done()
    finally:
        release.set()  # un hilo de to_thread no se puede cancelar
    c, _ = await pending
    assert c.name == "Slow"
