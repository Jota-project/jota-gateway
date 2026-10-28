"""#238: ningún texto crudo de excepción llega al cliente WS, a `last_error` ni a ningún log.

Cada prueba inyecta el marcador `SECRET-INTERNAL` en una excepción/frame y comprueba que no
aparece en lo que sale del gateway.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from src.services import transcriber_client as tc_mod
from src.services import tts_client as tts_mod
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import ClientRegistry
from src.services.protocol import OrchestratorEvent
from src.services.reconnection import ConnectionState, describe_error
from src.services.speech.ports import SttCallbacks, SttParams
from src.services.transcriber_client import TranscriberClient
from src.services.transcriber_reconnecting import ReconnectingTranscriberClient
from src.services.tts_client import TTSClient
from tests.unit.test_bridge_m6c_turns import _bridge

SECRET = "SECRET-INTERNAL"


def _logged(caplog) -> str:
    # Todos los niveles; format() incluye el traceback de exc_info, que lleva el mensaje.
    fmt = logging.Formatter()
    return "\n".join(fmt.format(r) for r in caplog.records if r.levelno >= logging.DEBUG)


async def test_turn_error_from_raw_runtime_error_is_internal_error(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, ws = _bridge()

    async def s(*a, **k):
        raise RuntimeError(SECRET)
        yield  # pragma: no cover

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")

    err = ws.of("error")[0]
    assert err["code"] == "TURN_ERROR" and err["message"] == "internal_error"
    assert "RuntimeError at " in _logged(caplog)  # traceback sin mensaje a DEBUG
    assert SECRET not in json.dumps(ws.sent, default=str)
    assert SECRET not in _logged(caplog)


async def test_turn_error_relays_orchestrator_error_code():
    bridge, ws = _bridge()

    async def s(*a, **k):
        yield OrchestratorEvent(type="error", content="turn_timeout")

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")

    assert ws.of("error")[0]["message"] == "turn_timeout"


async def test_openclaw_slow_retry_last_error_has_no_raw_text(caplog):
    caplog.set_level(logging.DEBUG)
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError(SECRET)
    roc = ReconnectingOpenClawClient(
        inner, "test", initial_backoff=0.01, max_backoff=0.02, max_duration=0.05
    )
    await roc.connect()
    for _ in range(100):
        if roc._reconnect_exhausted:
            break
        await asyncio.sleep(0.01)
    assert roc.state == ConnectionState.DEGRADED
    before = inner.connect.call_count
    for _ in range(100):  # al menos un intento del reintento lento
        if inner.connect.call_count > before:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)
    assert inner.connect.call_count > before

    assert roc.status().last_error == "RuntimeError"
    assert SECRET not in _logged(caplog)
    await roc.close()


class _FakeWS:
    def __init__(self, recv=None, frames=()):
        self._recv = recv
        self._frames = list(frames)
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self):
        return self._recv()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


async def test_transcriber_handshake_error_does_not_leak_message(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    frame = json.dumps({"type": "error", "code": "AUTH_FAILED", "message": SECRET})
    monkeypatch.setattr(
        tc_mod.websockets, "connect", AsyncMock(return_value=_FakeWS(recv=lambda: frame))
    )

    with pytest.raises(Exception) as exc_info:
        await TranscriberClient("x:1", "u").connect()

    assert SECRET not in str(exc_info.value)
    assert SECRET not in _logged(caplog)
    # Texto estable y útil para depurar, no un "Exception" pelado.
    assert describe_error(exc_info.value) == "transcriber_handshake_failed[AUTH_FAILED]"
    assert "transcriber_handshake_failed[AUTH_FAILED]" in _logged(caplog)


async def test_transcriber_handshake_code_is_sanitized(monkeypatch):
    frame = json.dumps({"type": "error", "code": f"{SECRET} x", "message": "m"})
    monkeypatch.setattr(
        tc_mod.websockets, "connect", AsyncMock(return_value=_FakeWS(recv=lambda: frame))
    )
    with pytest.raises(Exception) as exc_info:
        await TranscriberClient("x:1", "u").connect()
    assert describe_error(exc_info.value) == "transcriber_handshake_failed[unknown]"


async def test_transcriber_unexpected_handshake_frame_is_stable(monkeypatch):
    frame = json.dumps({"type": SECRET})
    monkeypatch.setattr(
        tc_mod.websockets, "connect", AsyncMock(return_value=_FakeWS(recv=lambda: frame))
    )
    with pytest.raises(Exception) as exc_info:
        await TranscriberClient("x:1", "u").connect()
    assert describe_error(exc_info.value) == "transcriber_handshake_failed[unexpected_frame]"


async def test_transcriber_error_and_warning_frames_do_not_log_message(caplog):
    caplog.set_level(logging.DEBUG)
    t = TranscriberClient("x:1", "u")
    t.ws = _FakeWS(
        frames=[
            json.dumps({"type": "warning", "code": "buffer_full", "message": SECRET}),
            json.dumps({"type": "error", "code": "E1", "message": SECRET}),
        ]
    )
    t._is_ready = True
    await t.listen_loop(AsyncMock())

    assert SECRET not in _logged(caplog)


async def test_tts_close_during_auth_does_not_leak_close_reason(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def _closed():
        raise ConnectionClosed(Close(1008, SECRET), None)

    monkeypatch.setattr(
        tts_mod.websockets, "connect", AsyncMock(return_value=_FakeWS(recv=_closed))
    )

    with pytest.raises(RuntimeError) as exc_info:
        await TTSClient("x:1", "tok", "u").connect()

    assert SECRET not in str(exc_info.value)
    assert SECRET not in _logged(caplog)


async def test_tts_error_frame_does_not_log_message(caplog):
    caplog.set_level(logging.DEBUG)
    t = TTSClient("x:1", "tok", "u")
    t.ws = _FakeWS(frames=[json.dumps({"type": "error", "code": "E1", "message": SECRET})])
    async for _ in t.get_audio_stream():
        pass

    assert SECRET not in _logged(caplog)


def _boom(*a, **k):
    raise RuntimeError(SECRET)


async def _raise_secret():
    raise RuntimeError(SECRET)


async def test_bridge_input_loop_error_logs_type_only(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, ws = _bridge()
    ws.receive = _raise_secret
    await bridge._client_input_loop()
    assert SECRET not in _logged(caplog)
    assert "RuntimeError at " in _logged(caplog)


async def test_bridge_supervised_task_crash_logs_type_only(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _ = _bridge()
    task = bridge._spawn(_raise_secret(), "some_task")
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # done-callback
    assert "RuntimeError" in _logged(caplog)
    assert SECRET not in _logged(caplog)


async def test_bridge_transcription_send_failure_logs_type_only(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _ = _bridge()
    bridge._sender = AsyncMock()
    bridge._sender.send_json.side_effect = _boom
    await bridge._on_transcription("hola", True)
    assert SECRET not in _logged(caplog)


async def test_bridge_close_all_failed_active_turn_logs_type_only(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _ = _bridge()

    async def _turn():
        await asyncio.sleep(0.01)
        raise RuntimeError(SECRET)

    bridge._active_turn = asyncio.create_task(_turn())
    await bridge.close_all()
    assert SECRET not in _logged(caplog)


async def test_transcriber_listen_crash_logs_no_message(caplog):
    caplog.set_level(logging.DEBUG)
    cb = SttCallbacks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock())
    w = ReconnectingTranscriberClient(
        url="test:1", client_id="c1", callbacks=cb, params=SttParams("es", 0.0)
    )
    w.state = ConnectionState.CONNECTED

    async def boom(*a, **k):
        raise RuntimeError(SECRET)

    w._client.listen_loop = boom
    await w.run()
    assert w.state == ConnectionState.DEGRADED
    assert SECRET not in _logged(caplog)


async def test_registry_shutdown_drain_failure_logs_type_only(caplog):
    caplog.set_level(logging.DEBUG)
    reg = ClientRegistry()
    bridge = AsyncMock()
    bridge.close_all.side_effect = _boom
    reg.register("u", bridge)
    await reg.close_all_sessions(status="shutdown", timeout=1.0)
    assert SECRET not in _logged(caplog)

