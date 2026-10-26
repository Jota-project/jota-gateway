"""#238: ningún texto crudo de excepción llega al cliente WS, a `last_error` ni a logs INFO+.

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
from src.services.protocol import OrchestratorEvent
from src.services.reconnection import ConnectionState
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient
from tests.unit.test_bridge_m6c_turns import _bridge

SECRET = "SECRET-INTERNAL"


def _info_plus(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)


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
    assert SECRET not in json.dumps(ws.sent, default=str)
    assert SECRET not in _info_plus(caplog)


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
    assert SECRET not in _info_plus(caplog)
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
    assert SECRET not in _info_plus(caplog)


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

    assert SECRET not in _info_plus(caplog)


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
    assert SECRET not in _info_plus(caplog)


async def test_tts_error_frame_does_not_log_message(caplog):
    caplog.set_level(logging.DEBUG)
    t = TTSClient("x:1", "tok", "u")
    t.ws = _FakeWS(frames=[json.dumps({"type": "error", "code": "E1", "message": SECRET})])
    async for _ in t.get_audio_stream():
        pass

    assert SECRET not in _info_plus(caplog)

