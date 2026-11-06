"""Verificadores de proveedores de `jota-gw onboard` contra los fakes reales (nunca la red)."""

import asyncio
import logging
import socket

import pytest
import websockets

from jota_gateway.cli import verify
from jota_gateway.cli.verify import (
    NOT_LOOPBACK_DETAIL,
    detect_openclaw,
    is_loopback,
    verify_elevenlabs,
    verify_jota_stt,
    verify_jota_tts,
    verify_openclaw,
)
from jota_gateway.core.config import settings
from tests.fakes.elevenlabs_stt import FakeElevenLabsStt
from tests.fakes.elevenlabs_tts import FakeElevenLabsTts
from tests.fakes.jota_upstreams import FakeTranscriber, FakeTts
from tests.fakes.openclaw import FakeOpenClawServer

# Corto a propósito: websockets recorta los frames largos a DEBUG y uno largo no saldría entero.
TOKEN = "oc-SECRET-42"


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ws_debug_unpinned():
    """Deshace el pin de `websockets` que haya dejado otro test: el verificador debe ponerlo."""
    loggers = [logging.getLogger(n) for n in ("websockets", "websockets.client")]
    saved = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.NOTSET)
    yield
    for lg, level in zip(loggers, saved):
        lg.setLevel(level)


def test_is_loopback(monkeypatch):
    def no_dns(*a, **kw):
        raise AssertionError("is_loopback no resuelve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(socket, "gethostbyname", no_dns)
    for host in ("127.0.0.1", "127.1.2.3", "::1", "localhost"):
        assert is_loopback(host), host
    for host in ("192.168.1.19", "example.com", "0.0.0.0", "localhost.example.com", ""):
        assert not is_loopback(host), host


async def test_verify_openclaw_ok():
    async with FakeOpenClawServer(token=TOKEN) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (True, "ok")
    assert TOKEN not in v.detail


async def test_verify_openclaw_bad_token_rejected(caplog, ws_debug_unpinned):
    caplog.set_level(logging.DEBUG)
    async with FakeOpenClawServer(token="otro-token") as server:
        v = await verify_openclaw("localhost", server.port, TOKEN)
    assert (v.ok, v.reason) == (False, "rejected")
    assert "OpenClaw rechazó el token" in v.detail
    assert TOKEN not in v.detail and TOKEN not in caplog.text  # ni el frame `connect` a DEBUG


async def test_verify_openclaw_remote_not_loopback_no_socket(monkeypatch):
    calls = []

    def no_connect(*a, **kw):
        calls.append(a)
        raise OSError("no debería conectar")

    monkeypatch.setattr(websockets, "connect", no_connect)
    async with FakeOpenClawServer(token=TOKEN) as server:
        v = await verify_openclaw("192.168.1.19", server.port, TOKEN)
        assert server.connections == 0
    assert (v.ok, v.reason, v.detail) == (False, "not_loopback", NOT_LOOPBACK_DETAIL)
    assert calls == []


async def test_verify_openclaw_still_starting_is_timeout_not_rejected(monkeypatch):
    """OpenClaw arrancando más allá del presupuesto de reintento no es un token rechazado."""
    monkeypatch.setattr("jota_gateway.services.openclaw.client._STARTUP_RETRY_BUDGET_S", 0.3)
    async with FakeOpenClawServer(token=TOKEN, startup_pending=10**6) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (False, "timeout")


async def test_verify_jota_stt_ok():
    fake = FakeTranscriber()
    await fake.start()
    try:
        v = await verify_jota_stt(f"127.0.0.1:{fake.port}", "")
    finally:
        await fake.stop()
    assert (v.ok, v.reason) == (True, "ok")
    assert len(fake.configs) == 1


async def test_verify_jota_stt_unreachable():
    port = closed_port()
    v = await verify_jota_stt(f"127.0.0.1:{port}", "tok")
    assert (v.ok, v.reason) == (False, "unreachable")
    assert f"127.0.0.1:{port}" in v.detail


async def test_verify_jota_tts_rejected_without_token():
    fake = FakeTts(token="tts-secret")
    await fake.start()
    try:
        v = await verify_jota_tts(f"127.0.0.1:{fake.port}", "")
    finally:
        await fake.stop()
    assert (v.ok, v.reason) == (False, "rejected")
    assert "sin clave" in v.detail
    assert "rechazó la conexión sin clave: este servicio exige clave" in v.detail


async def test_verify_jota_tts_wrong_token_rejected():
    fake = FakeTts(token="tts-secret")
    await fake.start()
    try:
        bad = await verify_jota_tts(f"127.0.0.1:{fake.port}", "tts-WRONG-secret")
        good = await verify_jota_tts(f"127.0.0.1:{fake.port}", "tts-secret")
    finally:
        await fake.stop()
    assert (bad.ok, bad.reason) == (False, "rejected")
    assert "sin clave" not in bad.detail and "tts-WRONG-secret" not in bad.detail
    assert (good.ok, good.reason) == (True, "ok")


async def test_verify_timeout_uses_module_deadline(monkeypatch):
    """Un servicio que acepta TCP y calla agota el plazo (constante de módulo)."""
    monkeypatch.setattr(verify, "VERIFY_TIMEOUT_S", 0.3)
    held = []  # sin referencia, el writer se recoge y la conexión se cierra
    server = await asyncio.start_server(lambda r, w: held.append(w), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        v = await verify_jota_stt(f"127.0.0.1:{port}", "")
        assert loop.time() - started < 2.0
    finally:
        for w in held:
            w.close()
        server.close()
    assert (v.ok, v.reason) == (False, "timeout")


@pytest.mark.parametrize("kind", ["stt", "tts"])
async def test_verify_elevenlabs_bad_key_not_echoed(monkeypatch, caplog, ws_debug_unpinned, kind):
    bad_key = "el-BAD-key-SECRET-9876543210"
    fake = FakeElevenLabsStt() if kind == "stt" else FakeElevenLabsTts()
    await fake.start()
    monkeypatch.setattr(settings, "ELEVENLABS_BASE_URL", fake.url)
    caplog.set_level(logging.DEBUG)
    try:
        v = await verify_elevenlabs(kind, bad_key)
        good = await verify_elevenlabs(kind, fake.api_key)
    finally:
        await fake.stop()
    assert (v.ok, v.reason) == (False, "rejected")
    assert bad_key not in v.detail and bad_key not in caplog.text
    assert (good.ok, good.reason) == (True, "ok")
    assert settings.ELEVENLABS_API_KEY is None  # nunca toca el singleton


async def test_verify_elevenlabs_unreachable_is_not_rejected(monkeypatch):
    monkeypatch.setattr(settings, "ELEVENLABS_BASE_URL", f"ws://127.0.0.1:{closed_port()}")
    v = await verify_elevenlabs("tts", "una-clave")
    assert (v.ok, v.reason) == (False, "unreachable")


@pytest.mark.parametrize(
    "call",
    [
        lambda: verify_openclaw("127.0.0.1", 0, TOKEN),
        lambda: verify_openclaw("127.0.0.1", 70000, TOKEN),
        lambda: verify_jota_stt("ws://localhost:9000", ""),
        lambda: verify_jota_tts("", ""),
        lambda: verify_elevenlabs("stt", "  "),
    ],
)
async def test_invalid_input_is_invalid_without_socket(monkeypatch, call):
    def no_connect(*a, **kw):
        raise AssertionError("no debería conectar")

    monkeypatch.setattr(websockets, "connect", no_connect)
    v = await call()
    assert (v.ok, v.reason) == (False, "invalid")


async def test_detect_openclaw_none_when_closed():
    assert await detect_openclaw(closed_port()) is None


async def test_detect_openclaw_live_without_version():
    """`/healthz` de OpenClaw 2026.11.1 no lleva versión: "" (encontrado), no None."""
    async with FakeOpenClawServer() as server:
        assert await detect_openclaw(server.port) == ""
