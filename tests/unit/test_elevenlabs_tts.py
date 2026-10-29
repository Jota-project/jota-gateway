"""Adaptador ElevenLabs TTS (WebSocket stream-input) contra el upstream falso."""

import asyncio
import logging
import socket
import time

import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.services.reconnection import ConnectionState
from src.services.speech.elevenlabs import common as common_mod
from src.services.speech.elevenlabs import tts as tts_mod
from src.services.speech.elevenlabs.tts import ElevenLabsTtsProvider
from tests.fakes.elevenlabs_tts import FakeElevenLabsTts

KEY = "fake-el-key-SECRET-0123456789"
VOICE = "gc61tTk93h3LBXPxew2V"
USER_TEXT = "frase privada del usuario que supera con mucho los cuarenta caracteres."


# Constantes de módulo que los tests ajustan vía make_settings (se restauran tras cada test).
_CONSTS = {"MAX_STREAMS": tts_mod, "MAX_BUFFER_BYTES": tts_mod, "HEALTH_TTL_S": common_mod}


@pytest.fixture(autouse=True)
def _restore_consts(monkeypatch):
    for name, mod in _CONSTS.items():
        monkeypatch.setattr(mod, name, getattr(mod, name))


def make_settings(fake_url: str, **update):
    for name in [k for k in update if k in _CONSTS]:
        setattr(_CONSTS[name], name, update.pop(name))
    base = {
        "ELEVENLABS_API_KEY": SecretStr(KEY),
        "ELEVENLABS_BASE_URL": fake_url,
        "ELEVENLABS_TTS_DEFAULT_VOICE": VOICE,
        "ELEVENLABS_TTS_MODEL": "eleven_flash_v2_5",
        "TTS_VOICE_MAP": {},
        "TTS_RECONNECT_INITIAL_BACKOFF": 1.0,
        "TTS_RECONNECT_MAX_BACKOFF": 60.0,
    }
    base.update(update)
    return settings.model_copy(update=base)


async def start_fake(**kw) -> FakeElevenLabsTts:
    fake = FakeElevenLabsTts(api_key=KEY, **kw)
    await fake.start()
    return fake


async def collect(stream, timeout=3):
    async def run():
        return [c async for c in stream.get_audio_stream()]

    return await asyncio.wait_for(run(), timeout)


async def wait_until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condición no cumplida a tiempo")


@pytest.fixture
async def fake():
    f = await start_fake()
    yield f
    await f.stop()


def header(conn, name):
    return {k.lower(): v for k, v in conn["headers"].items()}.get(name.lower())


async def test_init_message_and_query_params(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.send_text_chunk("Hola.")
    await stream.end()
    await collect(stream)
    await stream.close()
    conn = fake.connections[0]
    assert conn["path"] == f"/v1/text-to-speech/{VOICE}/stream-input"
    assert conn["query"] == {
        "model_id": "eleven_flash_v2_5",
        "output_format": "pcm_24000",
        "inactivity_timeout": "180",
    }
    assert header(conn, "xi-api-key") == KEY
    assert KEY not in conn["raw_path"]
    assert conn["messages"][0] == {
        "text": " ",
        "voice_settings": {"speed": 1.0},
        "generation_config": {"chunk_length_schedule": [120, 160, 250, 290]},
    }


async def test_flush_on_sentence_end(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, 1.0, "c1")
    for piece in ("Hola que tal", " estás bien. ", "Y tú", "?"):
        await stream.send_text_chunk(piece)
    await stream.end()
    await collect(stream)
    await stream.close()
    assert fake.messages[1:] == [
        {"text": "Hola que tal"},
        {"text": " estás bien. ", "flush": True},
        {"text": "Y tú"},
        {"text": "?", "flush": True},
        {"text": "", "flush": True},
        {"text": ""},
    ]
    assert stream.chars_sent == len("Hola que tal estás bien. Y tú?")


async def test_empty_chunk_is_not_sent_as_end_marker(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.send_text_chunk("")
    await stream.send_text_chunk("Hola.")
    await stream.end()
    await collect(stream)
    await stream.close()
    assert {"text": ""} not in fake.messages[1:-1]


async def test_end_sends_flush_then_empty_and_iterator_ends_on_isFinal(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.send_text_chunk("Hola mundo cruel.")
    await stream.end()
    chunks = await collect(stream)
    await stream.close()
    assert chunks and all(len(c) % 2 == 0 for c in chunks)
    assert fake.messages[-2:] == [{"text": "", "flush": True}, {"text": ""}]


async def test_connect_returns_immediately_with_slow_handshake():
    fake = await start_fake(delay_handshake_s=0.5)
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url))
        t0 = time.monotonic()
        stream = await p.connect(VOICE, 1.0, "c1")
        assert time.monotonic() - t0 < 0.05
        assert stream is not None
        await stream.close()
    finally:
        await fake.stop()


async def test_close_midway_under_1s_and_releases_semaphore(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url, MAX_STREAMS=1))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.send_text_chunk("a medias")
    await wait_until(lambda: len(fake.messages) >= 2)
    t0 = time.monotonic()
    await stream.close()
    await stream.close()  # idempotente
    assert time.monotonic() - t0 < 1.0
    assert await collect(stream) == []
    again = await p.connect(VOICE, 1.0, "c1")
    assert again is not None
    await again.close()


async def test_cancel_during_connect_releases_semaphore():
    fake = await start_fake(delay_handshake_s=1.0)
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url, MAX_STREAMS=1))
        stream = await p.connect(VOICE, 1.0, "c1")
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await stream.close()
        assert time.monotonic() - t0 < 1.0
        again = await p.connect(VOICE, 1.0, "c1")
        assert again is not None
        await again.close()
    finally:
        await fake.stop()


async def test_close_before_runner_starts_releases_semaphore(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url, MAX_STREAMS=1))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.close()  # la tarea aún no ha ejecutado ni su primera línea
    again = await p.connect(VOICE, 1.0, "c1")
    assert again is not None
    await again.close()


async def test_semaphore_full_returns_none_without_touching_health(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url, MAX_STREAMS=1))
    events = []
    p.on_state_change = events.append
    first = await p.connect(VOICE, 1.0, "c1")
    assert await p.connect(VOICE, 1.0, "c2") is None
    assert p.state == ConnectionState.CONNECTED
    assert events == []
    assert p.status().reconnect_attempts == 0
    assert p.counters()["capacity_refusals"] == 1
    await first.close()


async def test_invalid_key_marks_provider_degraded_and_next_connect_none():
    fake = await start_fake()
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url, ELEVENLABS_API_KEY=SecretStr("wrong")))
        events = []
        p.on_state_change = events.append
        stream = await p.connect(VOICE, 1.0, "c1")
        await stream.send_text_chunk("Hola.")
        await stream.end()
        assert await collect(stream) == []
        await stream.close()
        assert p.state == ConnectionState.DEGRADED
        assert events == [ConnectionState.DEGRADED]
        n = len(fake.connections)
        assert await p.connect(VOICE, 1.0, "c1") is None
        assert len(fake.connections) == n  # sin red
        assert p.counters()["auth_errors"] == 1
        assert p.status().last_error == "auth"
        assert await p.probe() is False
        assert len(fake.connections) == n
    finally:
        await fake.stop()


async def test_handshake_429_counts_capacity_not_auth():
    fake = await start_fake(handshake_status=429)
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url))
        stream = await p.connect(VOICE, 1.0, "c1")
        assert await collect(stream) == []
        await stream.close()
        c = p.counters()
        assert c["provider_429"] == 1 and c["auth_errors"] == 0
        assert p.state == ConnectionState.RECONNECTING  # backoff normal, no terminal
        assert p.status().last_error == "capacity"
        assert await p.connect(VOICE, 1.0, "c1") is None  # dentro de la ventana de backoff
    finally:
        await fake.stop()


async def test_unexpected_close_midturn_reopens_once():
    fake = await start_fake(close_midway_after_n_messages=3)
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url))
        stream = await p.connect(VOICE, 1.0, "c1")
        await stream.send_text_chunk("uno dos tres ")
        await stream.send_text_chunk("cuatro cinco ")
        await wait_until(lambda: len(fake.connections) == 2)
        await stream.send_text_chunk("seis siete.")
        await stream.end()
        chunks = await collect(stream)
        await stream.close()
        assert chunks
        assert len(fake.connections) == 2
        assert fake.connections[1]["messages"][0]["text"] == " "  # re-envía el init
        assert fake.connections[1]["messages"][-1] == {"text": ""}
    finally:
        await fake.stop()


async def test_second_unexpected_close_does_not_reopen_again():
    fake = await start_fake(
        close_midway_after_n_messages=2, close_midway_only_first_connection=False
    )
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url))
        stream = await p.connect(VOICE, 1.0, "c1")
        await stream.send_text_chunk("uno dos ")
        await wait_until(lambda: len(fake.connections) == 2)
        await stream.send_text_chunk("tres cuatro ")
        await collect(stream)  # termina sin audio y sin colgarse
        await stream.close()
        await asyncio.sleep(0.1)
        assert len(fake.connections) == 2
    finally:
        await fake.stop()


async def test_buffer_cap_stops_stream_with_warning(fake, caplog):
    p = ElevenLabsTtsProvider(make_settings(fake.url, MAX_BUFFER_BYTES=1500))
    stream = await p.connect(VOICE, 1.0, "c1")
    with caplog.at_level(logging.WARNING):
        await stream.send_text_chunk("x" * 60 + ". ")  # ~7 chunks de 960 bytes
        await stream.end()
        await wait_until(lambda: stream._task.done())
    chunks = await collect(stream)
    await stream.close()
    assert 0 < len(chunks) < 7
    assert sum(len(c) for c in chunks) <= 1500
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "búfer" in r.getMessage()]
    assert len(warns) == 1


@pytest.mark.parametrize(
    ("requested", "sent"), [(2.0, 1.2), (0.1, 0.7), (None, 1.0), (0.9, 0.9)]
)
async def test_speed_clamped(fake, requested, sent):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, requested, "c1")
    await stream.end()
    await collect(stream)
    await stream.close()
    assert fake.connections[0]["messages"][0]["voice_settings"]["speed"] == sent


async def test_speed_clamp_logged_once_per_client(fake, caplog):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            s = await p.connect(VOICE, 5.0, "c1")
            await s.close()
    assert len([r for r in caplog.records if "velocidad" in r.getMessage()]) == 1


async def test_resolve_voice_kokoro_id_falls_back_to_default(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url, TTS_VOICE_MAP={"alice": "AAAAAAAAAAAAAAAAAAAA"}))
    assert p.resolve_voice("af_heart") == VOICE
    assert p.resolve_voice(None) == VOICE
    assert p.resolve_voice("zzzzzzzzzzzzzzzzzzzz") == "zzzzzzzzzzzzzzzzzzzz"
    assert p.resolve_voice("alice") == "AAAAAAAAAAAAAAAAAAAA"


async def test_keepalive_when_idle(fake, monkeypatch):
    monkeypatch.setattr(tts_mod, "KEEPALIVE_S", 0.1)
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.send_text_chunk("Hola")
    await wait_until(lambda: fake.messages.count({"text": " "}) >= 1)
    await stream.close()


async def test_no_key_or_text_in_logs_at_debug(fake, caplog):
    with caplog.at_level(logging.DEBUG):
        p = ElevenLabsTtsProvider(make_settings(fake.url))
        stream = await p.connect(VOICE, 1.0, "c1")
        await stream.send_text_chunk(USER_TEXT)
        await stream.end()
        await collect(stream)
        await stream.close()
        bad = ElevenLabsTtsProvider(make_settings(fake.url, ELEVENLABS_API_KEY=SecretStr("nope")))
        s = await bad.connect(VOICE, 1.0, "c2")
        await s.send_text_chunk(USER_TEXT)
        await collect(s)
        await s.close()
        await p.probe()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert KEY not in logged and "nope" not in logged
    assert USER_TEXT not in logged and "privada" not in logged
    assert "Invalid API key" not in logged


async def test_probe_ok_is_cached_and_cheap(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    assert await p.probe() is True
    assert await p.probe() is True
    assert len(fake.connections) == 1
    assert [m.get("text") for m in fake.connections[0]["messages"]] == [" ", ""]


async def test_probe_ttl_expiry_probes_again(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url, HEALTH_TTL_S=0.05))
    assert await p.probe() is True
    await asyncio.sleep(0.1)
    assert await p.probe() is True
    assert len(fake.connections) == 2


async def test_probe_unreachable_false_and_records_failure():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = ElevenLabsTtsProvider(make_settings(f"ws://127.0.0.1:{port}"))
    assert await p.probe() is False
    assert p.state == ConnectionState.RECONNECTING
    assert p.status().last_error == "ConnectionRefusedError"


async def test_start_never_raises_and_aclose_blocks_new_connects(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    await p.start()
    assert len(fake.connections) == 1
    await p.aclose()
    assert await p.connect(VOICE, 1.0, "c1") is None
    assert p.name == "elevenlabs"


async def test_connect_without_voice_uses_default_voice(fake):
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(None, None, "c1")
    await stream.end()
    await collect(stream)
    await stream.close()
    assert fake.connections[0]["path"] == f"/v1/text-to-speech/{VOICE}/stream-input"


async def _silent_tcp_server():
    """Acepta TCP, lee y nunca contesta: el handshake WS no termina."""

    async def handle(reader, writer):
        try:
            await reader.read()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_probe_hung_handshake_is_failure_not_healthy(monkeypatch):
    monkeypatch.setattr(tts_mod, "PROBE_CONNECT_TIMEOUT_S", 0.3)
    server, port = await _silent_tcp_server()
    try:
        p = ElevenLabsTtsProvider(make_settings(f"ws://127.0.0.1:{port}"))
        t0 = time.monotonic()
        assert await p.probe() is False
        assert time.monotonic() - t0 < 2.0
        assert p.state == ConnectionState.RECONNECTING
        assert await p.probe() is False  # en backoff, no cacheado como sano
    finally:
        server.close()


async def test_probe_timeout_after_successful_connect_counts_as_success(monkeypatch):
    import websockets

    async def handler(ws):
        await ws.wait_closed()  # handshake hecho, pero nunca cierra (hasta que lo cierra el probe)

    monkeypatch.setattr(tts_mod, "PROBE_TIMEOUT_S", 0.5)
    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        p = ElevenLabsTtsProvider(make_settings(f"ws://127.0.0.1:{port}"))
        assert await p.probe() is True
        assert p.state == ConnectionState.CONNECTED


async def test_idle_stream_without_end_closes_itself_at_turn_timeout(fake, monkeypatch, caplog):
    monkeypatch.setattr(tts_mod, "KEEPALIVE_S", 0.05)
    monkeypatch.setattr(tts_mod.settings, "TURN_TIMEOUT_S", 0.3)
    p = ElevenLabsTtsProvider(make_settings(fake.url))
    stream = await p.connect(VOICE, 1.0, "c1")
    await stream.send_text_chunk("Hola")
    with caplog.at_level(logging.WARNING):
        assert await collect(stream, timeout=3) == []  # el iterador termina solo
    assert stream._task.done()
    assert p._sem._value == 10  # hueco liberado
    assert sum("más allá del tope" in r.getMessage() for r in caplog.records) == 1
    assert "Hola" not in caplog.text
    keepalives = fake.messages.count({"text": " "})
    await asyncio.sleep(0.2)
    assert fake.messages.count({"text": " "}) == keepalives  # sin más keepalives


async def test_capacity_close_after_handshake_advances_backoff_but_not_health():
    fake = await start_fake(close_after_s=0.1, close_reason="concurrent limit exceeded")
    try:
        p = ElevenLabsTtsProvider(make_settings(fake.url))
        stream = await p.connect(VOICE, 1.0, "c1")
        assert await collect(stream) == []
        await stream.close()
        assert p.counters()["provider_429"] == 1
        assert await p.connect(VOICE, 1.0, "c1") is None  # dentro de la ventana de backoff
        assert p.state == ConnectionState.CONNECTED  # la capacidad no es una caída
        assert p.counters()["capacity_refusals"] == 0
    finally:
        await fake.stop()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hola.", True),
        ("¿Qué tal?", True),
        ("¡Genial!", True),
        ("Bueno…", True),
        ("Dijo «sí.» ", True),
        ('Dijo "vale."', True),
        ("Fin (o no.) ", True),
        ("Una frase. Otra", True),
        ("Hola:", False),
        ("Primero;", False),
        ("Lista: ", False),
        ("Hola", False),
        ("Buenos días, Sr.", False),
        ("Habla con la Dra. ", False),
        ("manzanas, peras, etc.", False),
        ("Paso 3.", False),
        ("Hola Sr. Pérez, qué tal", False),
        ("Dijo J. Pérez", False),
        ("Hola Sr. Pérez. Adiós", True),
        ("En 2024.", True),
    ],
)
def test_ends_sentence_heuristic(text, expected):
    assert tts_mod._ends_sentence(text) is expected
