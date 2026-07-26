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


@pytest.mark.asyncio
async def test_invalidate_advances_generation_counter():
    engine = _engine_with(ClientRecord(name="Gen", client_key="gen-key"))
    client = DbClient(engine=engine)
    await client.get_session("gen-key")
    assert client._generations.get("gen-key", 0) == 0

    client.invalidate("gen-key")
    assert client._generations["gen-key"] == 1

    client.invalidate("gen-key")
    assert client._generations["gen-key"] == 2


def test_generation_guard_prevents_stale_repopulation_after_concurrent_invalidate(
    monkeypatch, tmp_path
):
    """Si invalidate() corre en otro hilo mientras get_session() está en
    vuelo (incluso antes de que la consulta a BD arranque), el resultado
    obsoleto no debe repoblar el caché compartido.

    Motor de BD en fichero (sin `StaticPool`) para que el hilo lector y el
    hilo principal del test obtengan cada uno su propia conexión DBAPI real,
    igual que en `test_concurrent_readers_and_invalidate_never_leave_stale_cache`.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(ClientRecord(name="Before", client_key="race-key"))
        s.commit()

    client = DbClient(engine=engine)

    query_started = threading.Event()
    release_query = threading.Event()
    original_exec = Session.exec

    def slow_exec(self, *args, **kwargs):
        query_started.set()
        release_query.wait(timeout=5)
        return original_exec(self, *args, **kwargs)

    monkeypatch.setattr(Session, "exec", slow_exec)

    reader_result = {}

    def run_reader():
        reader_result["value"] = asyncio.run(client.get_session("race-key"))

    reader = threading.Thread(target=run_reader)
    reader.start()
    assert query_started.wait(timeout=5), "el lector nunca llegó a la consulta de BD"

    # Simula la mutación de admin completándose mientras la consulta del
    # lector sigue en vuelo: invalidate() corre en el hilo principal del
    # test, un hilo distinto al del lector.
    client.invalidate("race-key")

    release_query.set()
    reader.join(timeout=5)

    assert "value" in reader_result
    assert "race-key" not in client._session_cache


def test_concurrent_readers_and_invalidate_never_leave_stale_cache(tmp_path):
    """100 get_session concurrentes + 1 invalidate intercalado: una lectura
    final debe reflejar siempre el estado post-mutación, nunca el valor
    anterior a la invalidación.

    A diferencia de `_engine_with`, este motor usa un fichero SQLite real
    en disco (sin `StaticPool`) para que cada uno de los 100 hilos obtenga
    su propia conexión DBAPI real en lugar de contender por la única
    conexión compartida que `:memory:` + `StaticPool` fuerza. Con 100
    hilos golpeando una sola conexión SQLite compartida, SQLAlchemy/SQLite
    pueden devolver filas corruptas de forma esporádica, lo que hacía que
    este test fallase de forma silenciosa (excepciones perdidas dentro de
    los hilos) en vez de fallar de forma explícita.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(ClientRecord(name="v1", client_key="hammer-key"))
        s.commit()

    client = DbClient(engine=engine)

    reader_errors = []
    errors_lock = threading.Lock()

    def reader(idx):
        try:
            asyncio.run(client.get_session("hammer-key"))
        except Exception as exc:  # cualquier fallo del lector debe ser visible, no silencioso
            with errors_lock:
                reader_errors.append((idx, exc))

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()

    with Session(engine) as s:
        rec = s.exec(select(ClientRecord)).first()
        rec.name = "v2"
        s.add(rec)
        s.commit()
    client.invalidate("hammer-key")

    for t in threads:
        t.join(timeout=5)

    assert reader_errors == [], f"reader thread(s) raised: {reader_errors}"

    client_obj, _ = asyncio.run(client.get_session("hammer-key"))
    assert client_obj.name == "v2"


def test_rotated_key_does_not_poison_cache_when_read_races_the_commit(monkeypatch, tmp_path):
    """Simula rotate_client_key: la consulta de un lector arranca y LEE la
    fila con la key vieja ANTES de que el admin mute + commitee + invalide
    (orden fijado por el fix de #107 en admin_routes.py). El resultado
    obsoleto no debe repoblar el caché, y una lectura posterior de la key
    vieja debe fallar con ClientNotFound.

    Motor de BD en fichero (sin `StaticPool`) para que el hilo lector y el
    hilo principal del test obtengan cada uno su propia conexión DBAPI real,
    igual que en `test_concurrent_readers_and_invalidate_never_leave_stale_cache`.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(ClientRecord(name="R", client_key="old-key"))
        s.commit()

    client = DbClient(engine=engine)

    first_call_done = threading.Event()
    release_first_call = threading.Event()
    call_count = {"n": 0}
    count_lock = threading.Lock()
    original_exec = Session.exec

    def slow_exec(self, *args, **kwargs):
        result = original_exec(self, *args, **kwargs)
        with count_lock:
            call_count["n"] += 1
            is_first_call = call_count["n"] == 1
        if is_first_call:
            first_call_done.set()
            release_first_call.wait(timeout=5)
        return result

    monkeypatch.setattr(Session, "exec", slow_exec)

    reader_result = {}

    def run_reader():
        reader_result["value"] = asyncio.run(client.get_session("old-key"))

    reader = threading.Thread(target=run_reader)
    reader.start()
    assert first_call_done.wait(timeout=5), "el lector nunca completó su consulta de BD"

    with Session(engine) as s:
        rec = s.exec(select(ClientRecord)).first()
        rec.client_key = "new-key"
        s.add(rec)
        s.commit()
    client.invalidate("old-key")

    release_first_call.set()
    reader.join(timeout=5)

    # El lector, cuya lectura ganó la carrera contra el commit, obtiene
    # legítimamente el dato pre-rotación...
    reader_client, _ = reader_result["value"]
    assert reader_client.client_key == "old-key"
    # ...pero ese resultado obsoleto NUNCA debe repoblar el caché compartido.
    assert "old-key" not in client._session_cache

    # Cualquier lectura posterior debe reflejar la rotación ya asentada.
    with pytest.raises(ClientNotFound):
        asyncio.run(client.get_session("old-key"))
