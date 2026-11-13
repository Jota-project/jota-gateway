"""Verificadores de proveedores de `fogar-gw onboard` contra los fakes reales (nunca la red)."""

import asyncio
import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import websockets

from fogar_gateway.cli import verify
from fogar_gateway.cli.verify import (
    NOT_LOOPBACK_DETAIL,
    detect_openclaw,
    is_loopback,
    verify_elevenlabs,
    verify_fogar_stt,
    verify_fogar_tts,
    verify_openclaw,
)
from fogar_gateway.core.config import settings
from tests.fakes.elevenlabs_stt import FakeElevenLabsStt
from tests.fakes.elevenlabs_tts import FakeElevenLabsTts
from tests.fakes.fogar_upstreams import FakeTranscriber, FakeTts
from tests.fakes.openclaw import FakeOpenClawServer

ROOT = Path(__file__).resolve().parents[2]

# Corto a propósito: websockets recorta los frames largos a DEBUG y uno largo no saldría entero.
TOKEN = "oc-SECRET-42"


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def answering(*frames: str | bytes):
    """Servidor WS que manda `frames[0]` al conectar y el siguiente tras cada mensaje del cliente."""

    async def handler(ws):
        pending = list(frames)
        await ws.send(pending.pop(0))
        with contextlib.suppress(websockets.ConnectionClosed):
            async for _ in ws:
                if pending:
                    await ws.send(pending.pop(0))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


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
    for host in ("127.0.0.1", "127.1.2.3", "::1", "[::1]", "localhost"):
        assert is_loopback(host), host
    for host in ("192.168.1.19", "example.com", "0.0.0.0", "localhost.example.com", "", "[::1"):
        assert not is_loopback(host), host


async def test_verify_openclaw_ok():
    async with FakeOpenClawServer(token=TOKEN) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (True, "ok")
    assert TOKEN not in v.detail
    # M19: dice con qué acceso queda (en local, admin con el token compartido).
    assert v.detail == (f"Conectado a OpenClaw en 127.0.0.1:{server.port}. Con permisos de "
                        "administración (token compartido).")


async def test_verify_openclaw_without_admin_says_so():
    hello = {"type": "hello-ok", "protocol": 4, "policy": {"tickIntervalMs": 30000},
             "snapshot": {}, "auth": {"scopes": ["operator.read", "operator.write"]}}
    async with FakeOpenClawServer(token=TOKEN, hello_payload=hello) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (True, "ok")
    assert "Sin permisos de administración (token compartido; permisos: operator.read, " \
           "operator.write)" in v.detail


async def test_verify_openclaw_unannounced_scopes_are_unknown():
    hello = {"type": "hello-ok", "protocol": 4, "policy": {"tickIntervalMs": 30000},
             "snapshot": {}, "auth": {"role": "operator"}}
    async with FakeOpenClawServer(token=TOKEN, hello_payload=hello) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (True, "ok")
    assert "permisos desconocidos" in v.detail and "ninguno" not in v.detail


@pytest.fixture
def own_home(monkeypatch, tmp_path):
    """Base propia: el DeviceStore por defecto de verify_openclaw lee y escribe aquí."""
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    return tmp_path


async def _pair_gateway(server) -> None:
    """El gateway (con el DeviceStore por defecto) se empareja y guarda su token de dispositivo."""
    from fogar_gateway.services.openclaw.client import OpenClawClient, OrchestratorPairingRequired
    from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
    from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry

    def make():
        turns = TurnRegistry()
        return OpenClawClient("127.0.0.1", server.port, TOKEN, turns, FrameDispatcher(turns, ClientRegistry()))

    with pytest.raises(OrchestratorPairingRequired):
        await make().connect()
    server.approve()
    gateway = make()
    await gateway.connect()
    await gateway.close()


async def test_verify_openclaw_tests_the_pasted_token_not_the_stored_device_token(own_home):
    """Con un token de dispositivo guardado para ese OpenClaw, un token compartido malo se
    rechaza (antes se conectaba con el de dispositivo y onboard guardaba un token sin probar)."""
    from fogar_gateway.services.openclaw.device import DeviceStore

    async with FakeOpenClawServer(token=TOKEN, remote=True) as server:
        await _pair_gateway(server)
        assert DeviceStore().token_for(f"127.0.0.1:{server.port}") is not None
        bad = await verify_openclaw("127.0.0.1", server.port, "token-malo")
        assert (bad.ok, bad.reason) == (False, "rejected")
        good = await verify_openclaw("127.0.0.1", server.port, TOKEN)
        assert (good.ok, good.reason) == (True, "ok")
        assert server.connects[-1]["auth"] == {"token": TOKEN}
        assert "emparejado como dispositivo" in good.detail


async def test_verify_openclaw_identity_problem_is_not_a_pairing_request(own_home, caplog):
    caplog.set_level(logging.DEBUG)
    device_file = own_home / "data" / "openclaw-device.json"
    device_file.parent.mkdir()
    device_file.write_text("{roto")
    async with FakeOpenClawServer(token=TOKEN, remote=True) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (False, "invalid")
    assert "identidad de dispositivo" in v.detail and "fichero de identidad" in v.detail
    assert "devices approve" not in v.detail and "acepta el token" not in v.detail


async def test_verify_openclaw_pairing_pending_is_not_a_rejection(caplog):
    """OpenClaw comprueba el token antes del emparejamiento: aceptado, falta aprobarlo."""
    caplog.set_level(logging.DEBUG)
    async with FakeOpenClawServer(token=TOKEN, remote=True) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
        assert list(server.pending) == ["req-1"]
    assert (v.ok, v.reason) == (True, "pairing_required")
    assert "`openclaw devices approve req-1`" in v.detail
    assert v.detail.startswith("OpenClaw acepta el token. ")  # se mandó el compartido
    assert TOKEN not in v.detail and TOKEN not in caplog.text


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
    monkeypatch.setattr("fogar_gateway.services.openclaw.client._STARTUP_RETRY_BUDGET_S", 0.3)
    async with FakeOpenClawServer(token=TOKEN, startup_pending=10**6) as server:
        v = await verify_openclaw("127.0.0.1", server.port, TOKEN)
    assert (v.ok, v.reason) == (False, "timeout")


async def test_verify_fogar_stt_ok():
    fake = FakeTranscriber()
    await fake.start()
    try:
        v = await verify_fogar_stt(f"127.0.0.1:{fake.port}", "")
    finally:
        await fake.stop()
    assert (v.ok, v.reason) == (True, "ok")
    assert len(fake.configs) == 1


async def test_verify_fogar_stt_rejected_without_token():
    fake = FakeTranscriber(token="stt-secret")
    await fake.start()
    try:
        v = await verify_fogar_stt(f"127.0.0.1:{fake.port}", "")
    finally:
        await fake.stop()
    assert (v.ok, v.reason) == (False, "rejected")
    assert "sin clave" in v.detail


async def test_verify_fogar_stt_wrong_token_rejected():
    fake = FakeTranscriber(token="stt-secret")
    await fake.start()
    try:
        bad = await verify_fogar_stt(f"127.0.0.1:{fake.port}", "stt-WRONG-secret")
        good = await verify_fogar_stt(f"127.0.0.1:{fake.port}", "stt-secret")
    finally:
        await fake.stop()
    assert (bad.ok, bad.reason) == (False, "rejected")
    assert "sin clave" not in bad.detail and "stt-WRONG-secret" not in bad.detail
    assert (good.ok, good.reason) == (True, "ok")


_CHALLENGE = json.dumps({"type": "event", "event": "connect.challenge", "payload": {}})


def _openclaw(port):
    return verify_openclaw("127.0.0.1", port, TOKEN)


def _stt(port):
    return verify_fogar_stt(f"127.0.0.1:{port}", "")


@pytest.mark.parametrize(
    ("frames", "verify_call"),
    [
        (("esto no es json",), _openclaw),
        ((_CHALLENGE, json.dumps({"type": "event", "event": "tick"})), _openclaw),
        ((_CHALLENGE, json.dumps({"type": "res", "id": "x"})), _openclaw),
        ((json.dumps({"type": "error", "message": "Configuration failed", "code": "CONFIG_ERROR"}),), _stt),
        (("esto no es json",), _stt),
        ((b"\x80\x81\x82",), _stt),  # UTF-8 inválido: UnicodeDecodeError, no JSONDecodeError
        ((json.dumps({"type": 5}),), _stt),
        ((json.dumps({"foo": 1}),), _stt),
        (("[]",), _stt),
        (("esto no es json",), lambda port: verify_fogar_tts(f"127.0.0.1:{port}", "")),
    ],
    ids=[
        "openclaw-malformed",
        "openclaw-event-instead-of-res",
        "openclaw-res-without-ok",
        "stt-config-error",
        "stt-non-json",
        "stt-binary",
        "stt-schema-type",
        "stt-schema-missing",
        "stt-non-object",
        "tts-non-json",
    ],
)
async def test_unexpected_answer_is_invalid_not_rejected(frames, verify_call):
    """Solo un rechazo de autenticación es `rejected`: nunca «exige clave» por otro fallo."""
    async with answering(*frames) as port:
        v = await verify_call(port)
    assert (v.ok, v.reason) == (False, "invalid")
    assert "respuesta inesperada" in v.detail


async def test_verify_fogar_stt_bad_port_is_unreachable_not_unexpected():
    """websockets.connect lanza un ValueError a secas: no es una respuesta inesperada."""
    v = await verify_fogar_stt("127.0.0.1:99999", "")
    assert (v.ok, v.reason) == (False, "unreachable")


async def test_verify_fogar_stt_unreachable():
    port = closed_port()
    v = await verify_fogar_stt(f"127.0.0.1:{port}", "tok")
    assert (v.ok, v.reason) == (False, "unreachable")
    assert f"127.0.0.1:{port}" in v.detail


async def test_verify_fogar_tts_rejected_without_token():
    fake = FakeTts(token="tts-secret")
    await fake.start()
    try:
        v = await verify_fogar_tts(f"127.0.0.1:{fake.port}", "")
    finally:
        await fake.stop()
    assert (v.ok, v.reason) == (False, "rejected")
    assert "sin clave" in v.detail
    assert "rechazó la conexión sin clave: este servicio exige clave" in v.detail


async def test_verify_fogar_tts_wrong_token_rejected():
    fake = FakeTts(token="tts-secret")
    await fake.start()
    try:
        bad = await verify_fogar_tts(f"127.0.0.1:{fake.port}", "tts-WRONG-secret")
        good = await verify_fogar_tts(f"127.0.0.1:{fake.port}", "tts-secret")
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
        v = await verify_fogar_stt(f"127.0.0.1:{port}", "")
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
        lambda: verify_fogar_stt("ws://localhost:9000", ""),
        lambda: verify_fogar_tts("", ""),
        lambda: verify_elevenlabs("stt", "  "),
    ],
)
async def test_invalid_input_is_invalid_without_socket(monkeypatch, call):
    def no_connect(*a, **kw):
        raise AssertionError("no debería conectar")

    monkeypatch.setattr(websockets, "connect", no_connect)
    v = await call()
    assert (v.ok, v.reason) == (False, "invalid")


def test_openclaw_deadline_covers_startup_retry_budget():
    """Ruling 1: el plazo de verify_openclaw cubre el reintento de OpenClaw arrancando."""
    from fogar_gateway.services.openclaw.client import _STARTUP_RETRY_BUDGET_S

    assert verify.OPENCLAW_VERIFY_TIMEOUT_S > _STARTUP_RETRY_BUDGET_S


# En un proceso nuevo: core.config construye `Settings()` al importarse y aquí ya está cargado.
# argv[1]: un puerto cerrado, nunca el 18789 de un OpenClaw real del equipo.
_INVALID_CONFIG_PROBE = """
import asyncio, json, sys
from fogar_gateway.cli.verify import verify_elevenlabs, verify_fogar_stt, verify_fogar_tts, verify_openclaw

async def main():
    for call in (
        verify_openclaw("127.0.0.1", int(sys.argv[1]), "t"),
        verify_fogar_stt("127.0.0.1:9", ""),
        verify_fogar_tts("127.0.0.1:9", ""),
        verify_elevenlabs("tts", "k"),
    ):
        v = await call
        print(json.dumps([v.reason, v.detail]))

asyncio.run(main())
"""


@pytest.mark.parametrize(
    "env",
    [{"OPENCLAW_PORT": "SECRET-abc"}, {"TTS_VOICE_MAP": "SECRET-{"}],
    ids=["validation-error", "settings-error"],
)
def test_invalid_config_is_invalid_verdict_not_exception(env):
    """Un config.env o una variable de entorno inválidos: `invalid` con campo y tipo, sin el valor."""
    # Base de ElevenLabs local: si la configuración pasara, nunca se sale a la red real.
    full_env = {**os.environ, "ELEVENLABS_BASE_URL": "ws://127.0.0.1:9", **env}
    r = subprocess.run(
        [sys.executable, "-c", _INVALID_CONFIG_PROBE, str(closed_port())],
        cwd=ROOT, env=full_env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    verdicts = [json.loads(line) for line in r.stdout.splitlines()]
    assert [reason for reason, _ in verdicts] == ["invalid"] * 4
    field = next(iter(env))
    for _, detail in verdicts:
        assert field in detail and "SECRET" not in detail


async def test_detect_openclaw_none_when_closed():
    assert await detect_openclaw(closed_port()) is None


async def test_detect_openclaw_live_without_version():
    """`/healthz` de OpenClaw 2026.11.1 no lleva versión: "" (encontrado), no None."""
    async with FakeOpenClawServer() as server:
        assert await detect_openclaw(server.port) == ""
