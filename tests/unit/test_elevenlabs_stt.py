"""Adaptador ElevenLabs STT (WebSocket realtime) contra el upstream falso."""

import asyncio
import logging
import math
import socket
import struct
import time

import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.models.schemas import Client
from src.services.reconnection import ConnectionState
from src.services.speech.elevenlabs.stt import ElevenLabsSttProvider
from src.services.speech.ports import SttCallbacks, SttParams
from tests.fakes.elevenlabs_stt import FakeElevenLabsStt

KEY = "fake-el-key-SECRET-0123456789"
USER_TEXT = "frase privada del usuario que supera con mucho los cuarenta caracteres."


def make_settings(url: str, **update):
    base = {
        "ELEVENLABS_API_KEY": SecretStr(KEY),
        "ELEVENLABS_BASE_URL": url,
        "STT_AUDIO_GATE": "off",
        "ELEVENLABS_STT_IDLE_CLOSE_S": 0.3,
        "ELEVENLABS_STT_DRAIN_S": 1.0,
        "ELEVENLABS_STT_CONNECT_BUFFER_S": 2.0,
        "ELEVENLABS_STT_MAX_SESSIONS": 15,
        "ELEVENLABS_STT_WARM_HOLD_S": 0.0,
        "ELEVENLABS_HEALTH_TTL_S": 60.0,
        "TRANSCRIBER_RECONNECT_INITIAL_BACKOFF": 0.05,
        "TRANSCRIBER_RECONNECT_MAX_BACKOFF": 0.2,
        "TRANSCRIBER_RECONNECT_MAX_DURATION": 5.0,
    }
    base.update(update)
    return settings.model_copy(update=base)


def frame(samples: int = 1600, amp: float = 0.1) -> bytes:
    """Float32 LE 16 kHz: 1600 muestras = 0,1 s = 3200 bytes PCM16."""
    return struct.pack(
        f"<{samples}f", *[amp * math.sin(i * 0.3) for i in range(samples)]
    )


def silence(samples: int = 1600) -> bytes:
    return struct.pack(f"<{samples}f", *([0.0] * samples))


class Rec:
    def __init__(self):
        self.events: list = []
        self.statuses: list = []
        self.warnings: list = []
        self.states: list = []

    def callbacks(self) -> SttCallbacks:
        async def on_tr(text, final):
            self.events.append((text, final))

        async def on_warn(code, msg):
            self.warnings.append(code)

        async def on_status(state, reason):
            self.statuses.append((state, reason))

        async def noop(*_a):
            return None

        return SttCallbacks(on_tr, on_warn, on_status, noop)


async def start_fake(**kw) -> FakeElevenLabsStt:
    fake = FakeElevenLabsStt(api_key=KEY, **kw)
    await fake.start()
    return fake


def open_session(provider, rec: Rec, cid="c1"):
    s = provider.open_session(
        client=Client(id=cid, client_key="k", is_active=True),
        params=SttParams(language="es", vad_threshold=0.0),
        callbacks=rec.callbacks(),
    )
    s.on_state_change = rec.states.append
    return s


async def wait_until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condición no cumplida a tiempo")


@pytest.fixture
async def env():
    """(fake, make) con `make(**settings) -> (provider, session, rec, runner)`; limpia al final."""
    made = []
    fakes = []

    async def build(fake_kw=None, **update):
        fake = await start_fake(**(fake_kw or {}))
        fakes.append(fake)
        provider = ElevenLabsSttProvider(make_settings(fake.url, **update))
        rec = Rec()
        session = open_session(provider, rec)
        await session.start()
        runner = asyncio.create_task(session.run())
        made.append((session, runner))
        return fake, provider, session, rec

    yield build
    for session, runner in made:
        await session.close()
        await asyncio.gather(runner, return_exceptions=True)
    for fake in fakes:
        await fake.stop()


async def test_query_params_and_header_without_key_in_url(env):
    fake, _p, s, _r = await env()
    await s.send_audio(frame())
    await wait_until(lambda: fake.connections)
    c = fake.connections[0]
    q = c["query"]
    assert c["path"] == "/v1/speech-to-text/realtime"
    assert q["model_id"] == "scribe_v2_realtime"
    assert q["audio_format"] == "pcm_16000"
    assert q["language_code"] == "es"
    assert q["commit_strategy"] == "vad"
    assert q["vad_silence_threshold_secs"] == "1.0"
    assert q["include_timestamps"] == "false"
    # Sin valor explícito no se envía: `false` solo vale en enterprise/trial (visto en real).
    assert "enable_logging" not in q
    assert c["headers"]["xi-api-key"] == KEY
    assert KEY not in c["raw_path"]


async def test_enable_logging_sent_only_when_configured(env):
    fake, _p, s, _r = await env(ELEVENLABS_STT_ENABLE_LOGGING=False)
    await s.send_audio(frame())
    await wait_until(lambda: fake.connections)
    assert fake.connections[0]["query"]["enable_logging"] == "false"


async def test_audio_converted_to_pcm16_base64_and_chunked_at_least_100ms(env):
    fake, _p, s, _r = await env()
    for _ in range(30):
        await s.send_audio(frame(160))  # 10 ms
    await wait_until(lambda: fake.audio_bytes >= 9600)
    assert fake.invalid_audio == []
    assert all(m["audio_bytes"] >= 3200 for m in fake.messages)
    assert fake.messages[0]["sample_rate"] == 16000
    assert all(m["commit"] is False for m in fake.messages)


async def test_lazy_open_no_upstream_without_audio(env):
    fake, p, s, _r = await env()
    await asyncio.sleep(0.4)
    assert fake.connections == []
    assert p.counters()["upstream_opens"] == 0


async def test_connect_buffer_keeps_first_words(env):
    fake, _p, s, _r = await env({"delay_session_started_s": 0.3})
    for _ in range(10):  # 1 s mientras se conecta
        await s.send_audio(frame())
    await wait_until(lambda: fake.audio_bytes >= 32000, 3)
    assert fake.audio_bytes == 32000


async def test_connect_buffer_drops_oldest_beyond_cap(env):
    fake, _p, s, _r = await env(
        {"delay_session_started_s": 0.3}, ELEVENLABS_STT_CONNECT_BUFFER_S=0.5
    )
    for _ in range(10):
        await s.send_audio(frame())
    await wait_until(lambda: fake.audio_bytes >= 16000, 3)
    await asyncio.sleep(0.1)
    assert fake.audio_bytes == 16000


async def test_send_end_commits_without_trailing_silence(env):
    fake, _p, s, rec = await env({"on_end": [("hola", True)]})
    for _ in range(3):
        await s.send_audio(frame())
    await s.send_end()
    await wait_until(lambda: ("hola", True) in rec.events)
    assert fake.commits == 1
    assert fake.audio_bytes == 9600  # nada añadido tras el último audio
    assert fake.messages[-1]["commit"] is True
    await s.send_end()  # sin audio nuevo: no-op
    await asyncio.sleep(0.1)
    assert fake.commits == 1


async def test_commit_throttled_spacing(env):
    fake, _p, s, _r = await env({"on_end": [("x", True)]}, ELEVENLABS_STT_IDLE_CLOSE_S=30.0)
    await s.send_audio(frame())
    await wait_until(lambda: fake.audio_bytes == 3200)
    await s.send_end()
    await s.send_audio(frame())
    await s.send_end()  # demasiado pronto: se difiere
    await wait_until(lambda: fake.commits == 2, 3)
    assert fake.commit_times[1] - fake.commit_times[0] >= 0.95


async def test_idle_close_commits_drains_then_closes(env):
    seen = {}
    fake, _p, s, rec = await env({"on_end": [("fin", True)], "commit_delay_s": 0.2})

    async def on_tr(text, final):
        seen["open_at_final"] = fake.sockets_open
        rec.events.append((text, final))

    s._cb = SttCallbacks(on_tr, s._cb.on_warning, s._cb.on_status, s._cb.on_incomplete)
    await s.send_audio(frame())
    await wait_until(lambda: ("fin", True) in rec.events, 3)
    assert seen["open_at_final"] == 1  # el final llegó antes de cerrar
    await wait_until(lambda: fake.sockets_open == 0)
    assert fake.commits == 1


async def test_no_new_upstream_until_previous_drained(env):
    fake, _p, s, rec = await env(
        {"on_audio": [("p", False)], "on_end": [("u1", True)], "commit_delay_s": 0.4}
    )
    await s.send_audio(frame())
    await wait_until(lambda: fake.commits == 1, 3)  # el reposo inició el drenado
    await s.send_audio(frame())  # llega mientras la anterior drena
    assert len(fake.connections) == 1
    await wait_until(lambda: len(fake.connections) == 2, 3)
    await wait_until(lambda: len(rec.events) >= 3, 3)
    assert rec.events[:3] == [("p", False), ("u1", True), ("p", False)]


async def test_semaphore_full_busy_then_ok_on_acquire(env):
    fake, p, s1, rec1 = await env(ELEVENLABS_STT_MAX_SESSIONS=1)
    rec2 = Rec()
    s2 = open_session(p, rec2, "c2")
    r2 = asyncio.create_task(s2.run())
    await s1.send_audio(frame())
    await wait_until(lambda: fake.connections)
    await s2.send_audio(frame())
    await s2.send_audio(frame())
    assert rec2.statuses == [("busy", "provider_capacity")]  # una sola vez
    assert p.counters()["capacity_refusals"] == 1  # por episodio, no por frame
    await s1.close()
    await wait_until(lambda: not p._sem.locked())
    await s2.send_audio(frame())
    assert rec2.statuses[-1] == ("ok", None)
    await s2.close()
    await asyncio.gather(r2, return_exceptions=True)


async def test_busy_cleared_by_idle_timer(env):
    fake, p, s1, _r1 = await env(ELEVENLABS_STT_MAX_SESSIONS=1)
    rec2 = Rec()
    s2 = open_session(p, rec2, "c2")
    r2 = asyncio.create_task(s2.run())
    await s1.send_audio(frame())
    await wait_until(lambda: fake.connections)
    await s2.send_audio(frame())
    assert rec2.statuses == [("busy", "provider_capacity")]
    await wait_until(lambda: rec2.statuses[-1] == ("ok", None), 3)
    await s2.close()
    await asyncio.gather(r2, return_exceptions=True)


async def test_auth_error_degrades_provider_and_blocks_new_upstreams(env):
    fake, p, s, rec = await env({"error_type": "auth_error"})
    await s.send_audio(frame())
    await wait_until(lambda: p.state == ConnectionState.DEGRADED)
    assert s.state == ConnectionState.DEGRADED
    assert ConnectionState.DEGRADED in rec.states
    n = len(fake.connections)
    s2 = open_session(p, Rec(), "c2")
    assert s2.state == ConnectionState.DEGRADED
    await s2.send_audio(frame())
    await s.send_audio(frame())
    await asyncio.sleep(0.2)
    assert len(fake.connections) == n
    await s2.close()


async def test_bad_key_close_1008_is_auth_failure():
    fake = await start_fake()
    try:
        p = ElevenLabsSttProvider(make_settings(fake.url, ELEVENLABS_API_KEY=SecretStr("wrong")))
        s = open_session(p, Rec())
        await s.send_audio(frame())
        await wait_until(lambda: p.state == ConnectionState.DEGRADED)
        assert p.counters()["auth_errors"] >= 1
        await s.close()
    finally:
        await fake.stop()


async def test_quota_exceeded_degrades_provider(env):
    fake, p, s, _r = await env({"error_type": "quota_exceeded"})
    await s.send_audio(frame())
    await wait_until(lambda: p.state == ConnectionState.DEGRADED)
    assert p.status().last_error == "quota"


async def test_rate_limited_maps_busy_provider_capacity(env):
    fake, p, s, rec = await env({"error_type": "rate_limited"})
    await s.send_audio(frame())
    await wait_until(lambda: ("busy", "provider_capacity") in rec.statuses)
    assert p.state == ConnectionState.CONNECTED
    assert p.counters()["provider_429"] == 1


async def test_unexpected_close_with_audio_reconnects(env):
    fake, _p, s, rec = await env({"drop_after_n_messages": 1})
    for _ in range(3):
        await s.send_audio(frame())
    await wait_until(lambda: ConnectionState.RECONNECTING in rec.states)
    await wait_until(lambda: rec.states[-1] == ConnectionState.CONNECTED, 3)
    assert rec.states[:2] == [ConnectionState.RECONNECTING, ConnectionState.CONNECTED]
    await s.send_audio(frame())
    await wait_until(lambda: len(fake.connections) == 2 and fake.connections[1]["audio_bytes"] > 0)


async def test_upstream_idle_close_is_silent(env):
    fake, _p, s, rec = await env(
        {"on_end": [("x", True)], "idle_close_s": 0.2}, ELEVENLABS_STT_IDLE_CLOSE_S=30.0
    )
    await s.send_audio(frame())
    await s.send_end()
    await wait_until(lambda: ("x", True) in rec.events)
    await wait_until(lambda: fake.sockets_open == 0, 3)
    await asyncio.sleep(0.3)
    assert rec.states == [] and len(fake.connections) == 1
    await s.send_audio(frame())  # el siguiente audio reabre
    await wait_until(lambda: len(fake.connections) == 2)


async def test_gate_energy_forwards_only_speech_and_commits_on_segment_end(env):
    fake, _p, s, rec = await env({"on_end": [("hablado", True)]}, STT_AUDIO_GATE="energy")
    for _ in range(10):
        await s.send_audio(silence())
    await asyncio.sleep(0.2)
    assert fake.connections == [] and fake.audio_bytes == 0
    for _ in range(5):
        await s.send_audio(frame())
    for _ in range(17):  # > 1,5 s de silencio: cierra el segmento
        await s.send_audio(silence())
    await wait_until(lambda: ("hablado", True) in rec.events, 3)
    assert fake.commits == 1
    assert fake.audio_bytes > 0


async def test_usage_counts_audio_seconds(env):
    fake, p, s, _r = await env()
    for _ in range(5):
        await s.send_audio(frame())
    await wait_until(lambda: fake.audio_bytes == 16000)
    u = s.usage()
    assert u["stt_audio_s"] == pytest.approx(0.5)
    assert u["stt_upstream_opens"] == 1
    assert p.counters()["audio_seconds_sent"] == pytest.approx(0.5)


async def test_close_idempotent_releases_semaphore_once(env):
    fake, p, s, _r = await env(ELEVENLABS_STT_MAX_SESSIONS=2)
    await s.send_audio(frame())
    await wait_until(lambda: fake.connections)
    assert p._sem._value == 1
    await s.close()
    await s.close()
    await wait_until(lambda: p._sem._value == 2)
    await asyncio.sleep(0.1)
    assert p._sem._value == 2


async def test_warm_hold_feeds_silence_after_idle(env):
    fake, _p, s, _r = await env(
        ELEVENLABS_STT_IDLE_CLOSE_S=0.2, ELEVENLABS_STT_WARM_HOLD_S=0.5
    )
    await s.send_audio(frame())
    await wait_until(lambda: fake.audio_bytes == 3200)
    await asyncio.sleep(0.5)
    assert fake.audio_bytes > 3200  # silencio PCM16 mantiene la conexión
    assert fake.sockets_open == 1
    await wait_until(lambda: fake.sockets_open == 0, 3)


async def test_no_key_or_text_in_logs_at_debug(env, caplog):
    fake, _p, s, rec = await env(
        {"on_audio": [(USER_TEXT, True)], "error_type": "commit_throttled"}
    )
    with caplog.at_level(logging.DEBUG):
        await s.send_audio(frame())
        await wait_until(lambda: rec.events)
        await asyncio.sleep(0.1)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert KEY not in logged and USER_TEXT not in logged


async def test_probe_ok_sends_no_audio_and_is_cached():
    fake = await start_fake()
    try:
        p = ElevenLabsSttProvider(make_settings(fake.url))
        assert await p.probe() is True
        assert await p.probe() is True
        assert len(fake.connections) == 1 and fake.messages == []
        assert fake.connections[0]["headers"]["xi-api-key"] == KEY
    finally:
        await fake.stop()


async def test_probe_bad_key_marks_auth_failed_and_start_never_raises():
    fake = await start_fake()
    try:
        p = ElevenLabsSttProvider(make_settings(fake.url, ELEVENLABS_API_KEY=SecretStr("bad")))
        await p.start()
        assert p.state == ConnectionState.DEGRADED
        assert await p.probe() is False
        assert len(fake.connections) == 1
    finally:
        await fake.stop()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    p2 = ElevenLabsSttProvider(make_settings(f"ws://127.0.0.1:{port}"))
    await p2.start()
    assert p2.state == ConnectionState.RECONNECTING


async def test_retire_closes_provider_after_last_session(env):
    fake, p, s, _r = await env()
    await p.retire()
    assert not p._closed
    await s.close()
    assert p._closed


async def test_probe_hung_handshake_is_failure_not_healthy(monkeypatch):
    from src.services.speech.elevenlabs import stt as stt_mod

    monkeypatch.setattr(stt_mod, "PROBE_CONNECT_TIMEOUT_S", 0.3)

    async def handle(reader, writer):
        try:
            await reader.read()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        p = ElevenLabsSttProvider(make_settings(f"ws://127.0.0.1:{port}"))
        t0 = time.monotonic()
        assert await p.probe() is False
        assert time.monotonic() - t0 < 2.0
        assert p.state == ConnectionState.RECONNECTING
    finally:
        server.close()


async def test_partial_transcript_updates_last_transcript_at(env):
    fake, p, s, _r = await env()
    from src.services.speech.elevenlabs.stt import _Upstream

    up = _Upstream()
    assert s.last_transcript_at is None
    await s._on_message(up, {"message_type": "partial_transcript", "text": ""})
    assert s.last_transcript_at is None  # un parcial vacío no es actividad
    await s._on_message(up, {"message_type": "partial_transcript", "text": "hola"})
    assert s.last_transcript_at is not None


async def test_bridge_watchdog_not_triggered_by_partials_without_commit(env, monkeypatch):
    """Habla continua: llegan parciales pero ningún commit; el watchdog no puede dar strikes."""
    from unittest.mock import AsyncMock

    import src.services.bridge as bridge_module
    from src.models.schemas import ClientConfig
    from src.services.speech.elevenlabs.stt import _Upstream
    from tests.unit.test_bridge_watchdog import _make_bridge

    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.05)
    _fake, _p, s, _r = await env()
    config = ClientConfig(silence_timeout_s=0.1, max_silence_turns=1)
    bridge, ws, _t = _make_bridge(config=config)
    bridge.transcriber = s
    bridge._first_audio_at = time.monotonic()
    bridge._audio_burst_start = bridge._first_audio_at
    bridge._last_audio_at = float("inf")
    bridge.close_all = AsyncMock()
    up = _Upstream()

    task = asyncio.create_task(bridge._transcription_watchdog())
    try:
        end = time.monotonic() + 0.5  # > silence_timeout_s + 2 x poll
        while time.monotonic() < end:
            await s._on_message(up, {"message_type": "partial_transcript", "text": "hablando"})
            await asyncio.sleep(0.02)
        degraded = [c for c in ws.send_json.call_args_list if c[0][0].get("state") == "degraded"]
        assert degraded == []
        bridge.close_all.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_permit_released_before_respawn_no_spurious_busy_at_limit(env):
    fake, p, s, rec = await env(
        {"on_audio": [("p", False)], "on_end": [("u1", True)], "commit_delay_s": 0.4},
        ELEVENLABS_STT_MAX_SESSIONS=1,
    )
    await s.send_audio(frame())
    await wait_until(lambda: fake.commits == 1, 3)  # el reposo inició el drenado
    await s.send_audio(frame())  # llega mientras drena: queda en el búfer
    await wait_until(lambda: len(fake.connections) == 2, 3)
    await wait_until(lambda: fake.connections[1]["audio_bytes"] >= 3200, 3)
    assert rec.statuses == []
    assert p.counters()["capacity_refusals"] == 0


async def test_concurrent_send_audio_during_reopen_opens_one_upstream(env):
    fake, p, s, rec = await env(ELEVENLABS_STT_MAX_SESSIONS=2)

    async def slow_status(state, reason):
        rec.statuses.append((state, reason))
        await asyncio.sleep(0.05)

    s._cb = SttCallbacks(s._cb.on_transcription, s._cb.on_warning, slow_status, s._cb.on_incomplete)
    s._busy = True
    s._busy_cause = "sem"
    await asyncio.gather(s.send_audio(frame()), s.send_audio(frame()))
    await wait_until(lambda: fake.connections)
    await asyncio.sleep(0.2)
    assert len(fake.connections) == 1
    assert p._sem._value == 1


async def test_reopen_after_exhaustion_restores_session(env):
    fake, p, s, rec = await env(
        {"drop_after_n_messages": 2},
        TRANSCRIBER_RECONNECT_MAX_DURATION=0.1,
    )
    await s.send_audio(frame())
    await wait_until(lambda: len(fake.messages) == 1)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead = sock.getsockname()[1]
    live_base = p._base
    p._base = f"ws://127.0.0.1:{dead}"
    await s.send_audio(frame())
    await wait_until(lambda: s.state == ConnectionState.DEGRADED, 5)
    p._base = live_base
    fake2 = await start_fake()
    try:
        p._base = fake2.url
        await s.send_audio(frame())
        await wait_until(lambda: s.state == ConnectionState.CONNECTED, 3)
        assert rec.states[-1] == ConnectionState.CONNECTED
        assert p.state == ConnectionState.CONNECTED
    finally:
        await s.close()
        await fake2.stop()


async def test_capacity_refusal_counted_once_per_episode(env):
    fake, p, s1, _r1 = await env(ELEVENLABS_STT_MAX_SESSIONS=1)
    s2 = open_session(p, Rec(), "c2")
    r2 = asyncio.create_task(s2.run())
    await s1.send_audio(frame())
    await wait_until(lambda: fake.connections)
    for _ in range(5):
        await s2.send_audio(frame())
    assert p.counters()["capacity_refusals"] == 1
    assert s2.usage()["stt_capacity_refusals"] == 1
    await s2.close()
    await asyncio.gather(r2, return_exceptions=True)


async def test_provider_warning_is_not_forwarded_as_degradation(env, caplog):
    """En real, ElevenLabs avisa en cada sesión (p. ej. zero retention no aplicado): no es
    una degradación del servicio, así que no llega al cliente."""
    import src.services.speech.elevenlabs.stt as stt_mod

    stt_mod._warning_logged[0] = False
    caplog.set_level(logging.INFO, logger=stt_mod.__name__)
    fake, _p, s, rec = await env(fake_kw={"error_type": "warning", "close_after_error": False})
    await s.send_audio(frame())
    await wait_until(lambda: fake.audio_bytes > 0)
    await asyncio.sleep(0.1)
    assert rec.warnings == []
    assert rec.statuses == []
    assert "aviso informativo del proveedor" in caplog.text
