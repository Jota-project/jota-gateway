"""Tests para WebSocket handshake (/ws/stream)."""

import asyncio
import logging
import queue
import re
import threading
import time
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import uvicorn
import websockets
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect

from fogar_gateway.api.routes import router as stream_router
from fogar_gateway.core.config import settings
from fogar_gateway.core.logging import fingerprint_key
from fogar_gateway.core.request_id import RequestIdMiddleware
from fogar_gateway.main import app
from fogar_gateway.services.outbound import ClientGone, QueuedSender
from tests.integration.conftest import CLIENT_ID, VALID_KEY


def _assert_request_id(message: str) -> None:
    match = re.search(r"\brequest_id=([0-9a-f-]{36})\b", message)
    assert match is not None
    assert UUID(match.group(1)).version == 4


def test_malformed_json_closes_ws(client):
    """JSON malformado como primer mensaje → WS se cierra (4000)."""
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws/stream") as ws:
        ws.send_text("not-json{{")
        ws.receive_text()  # debe lanzar excepción al recibir close frame
    assert (exc.value.code, exc.value.reason) == (4000, "invalid handshake")


def test_handshake_timeout_closes_ws(client, monkeypatch):
    """Cliente conecta pero nunca manda el JSON de handshake → cierre 4900
    tras HANDSHAKE_TIMEOUT_S (issue #115).

    Bounded via a daemon worker thread + queue.get(timeout=...), not
    pytest-timeout (not a dependency of this project — flagging rather than
    adding one). TestClient's websocket helpers are synchronous, so
    ws.receive_text() can't be wrapped in asyncio.wait_for the way the other
    three bounded-deadline mechanisms' tests are. If the HANDSHAKE_TIMEOUT_S
    enforcement in routes.py were ever reverted, the server would never close
    this idle connection and a plain `ws.receive_text()` call would block
    forever, hanging the whole test run instead of failing. Running it on a
    daemon thread means an actual regression still fails fast here (5s,
    generous relative to HANDSHAKE_TIMEOUT_S=0.05s below) — the abandoned
    thread doesn't block interpreter exit since it's a daemon.
    """
    monkeypatch.setattr(settings, "HANDSHAKE_TIMEOUT_S", 0.05)
    with client.websocket_connect("/ws/stream") as ws:
        result: queue.Queue = queue.Queue(maxsize=1)

        def _receive():
            try:
                ws.receive_text()  # nunca mandamos nada — debe llegar el close frame
                result.put(("ok", None))
            except Exception as exc:
                result.put(("exc", exc))

        threading.Thread(target=_receive, daemon=True).start()
        try:
            kind, exc = result.get(timeout=5.0)
        except queue.Empty:
            pytest.fail(
                "ws.receive_text() did not return within 5s — HANDSHAKE_TIMEOUT_S "
                "enforcement in routes.py appears to have regressed (the server "
                "never closed a connection that never sent a handshake)."
            )
        if kind == "ok":
            pytest.fail(
                "expected ws.receive_text() to raise once the server closes the "
                "connection on handshake timeout, but it returned normally"
            )
    assert isinstance(exc, WebSocketDisconnect)
    assert (exc.code, exc.reason) == (4900, "handshake timeout")


def test_invalid_client_key_log_is_safe_and_correlatable(client, caplog, monkeypatch):
    key = "bad-key\nforged-log-entry"
    source_ip = "198.51.100.23"
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1")

    # Use a mock to capture the warning call since caplog isn't capturing
    from unittest import mock

    warning_calls = []

    def mock_warning(msg, *args, **kwargs):
        warning_calls.append(msg if isinstance(msg, str) else msg % args)

    with mock.patch.object(logging.getLogger("fogar_gateway.api.routes"), "warning", mock_warning):
        ws = client.websocket_connect(
            "/ws/stream",
            headers={"x-real-ip": source_ip},
        )
        ws.__enter__()
        try:
            ws.send_json(
                {
                    "client_key": key,
                    "input_mode": "text",
                    "output_mode": ["text"],
                }
            )
            ws.receive_text()
        except Exception:
            pass
        finally:
            ws.__exit__(None, None, None)

    handshake_msgs = [m for m in warning_calls if "Handshake rechazado" in m]
    assert len(handshake_msgs) == 1
    message = handshake_msgs[0]
    assert f"source_ip={source_ip}" in message
    assert f"fp={fingerprint_key(key)}" in message
    _assert_request_id(message)
    assert key not in message
    assert "bad-key" not in message
    assert "forged-log-entry" not in message
    assert "\n" not in message


def test_missing_required_handshake_field_closes_ws(client):
    """Handshake sin campo requerido (input_mode) → WS se cierra."""
    with pytest.raises(Exception), client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": VALID_KEY, "output_mode": ["text"]})
        ws.receive_text()


def test_valid_text_mode_handshake_connection_stays_open(client, caplog):
    """Handshake válido — gateway responde con ready y la conexión permanece abierta."""
    from unittest import mock

    info_calls = []

    def mock_info(msg, *args, **kwargs):
        info_calls.append(msg if isinstance(msg, str) else msg % args)

    with mock.patch.object(logging.getLogger("fogar_gateway.api.routes"), "info", mock_info):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json(
                {
                    "client_key": VALID_KEY,
                    "input_mode": "text",
                    "output_mode": ["text"],
                }
            )
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["input_mode"] == "text"
            assert ready["output_mode"] == ["text"]
            assert "session_id" in ready
            assert "agent" in ready
            assert "requested_capabilities" in ready
            ws.send_text('{"type":"end"}')

    handshake_msgs = [m for m in info_calls if "Handshake verificado" in m]
    assert len(handshake_msgs) == 1
    message = handshake_msgs[0]
    assert f"client_id={CLIENT_ID}" in message
    assert f"fp={fingerprint_key(VALID_KEY)}" in message
    _assert_request_id(message)
    assert VALID_KEY not in message

    # Also verify the full caplog text doesn't contain the key
    assert VALID_KEY not in caplog.text


def test_ready_carries_requested_and_live_capabilities_when_audio(client, monkeypatch):
    """#114 — `ready` divide capabilities en requested/live."""
    from unittest.mock import AsyncMock

    from fogar_gateway.services.reconnection import ConnectionState

    # TTS ping returns False (degraded), transcriber state is CONNECTED
    monkeypatch.setattr(
        "fogar_gateway.services.tts_client.TTSClient.ping",
        AsyncMock(return_value=False),
    )

    # Mock transcriber state directly on the bridge after it's created
    # by patching the ReconnectingTranscriberClient class to report CONNECTED
    class FakeTranscriber:
        state = ConnectionState.CONNECTED

        last_transcript_at = None

        async def start(self):
            pass

        async def run(self):
            pass

        async def close(self):
            pass

    def fake_transcriber_factory(*a, **kw):
        return FakeTranscriber()

    monkeypatch.setattr(
        "fogar_gateway.services.speech.fogar.ReconnectingTranscriberClient",
        fake_transcriber_factory,
    )

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "audio",
                "output_mode": ["audio", "text"],
            }
        )
        frames = []
        for _ in range(10):
            try:
                frames.append(ws.receive_json())
            except Exception:
                break
            if any(f.get("type") == "ready" for f in frames):
                break
        ready = next(f for f in frames if f.get("type") == "ready")

    assert "capabilities" not in ready
    assert ready["requested_capabilities"] == {
        "barge_in": True,
        "tts": True,
        "transcriber": True,
    }
    assert ready["live_capabilities"] == {
        "barge_in": True,
        "tts": False,
        "transcriber": True,
    }
    status_before_ready = [
        f for f in frames if f.get("type") == "status" and f.get("service") == "tts"
    ]
    assert status_before_ready, "Debe haber un status tts unavailable antes de ready"


def test_ready_uses_both_sections_for_text_only_handshake(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "text",
                "output_mode": ["text"],
            }
        )
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert ready["requested_capabilities"] == {
        "barge_in": True,
        "tts": False,
        "transcriber": False,
    }
    assert ready["live_capabilities"] == {
        "barge_in": False,
        "tts": False,
        "transcriber": False,
    }


def test_handshake_validation_error_log_has_no_key_material(client, caplog):
    """G-27b (#216): el ERROR por campo obligatorio ausente no vuelca la client_key
    (pydantic incluye `input_value=` con el dict entero) — solo tipo y ubicación."""
    key = "Zq9xKLMNOPQRSTUVWXYZabcdefghij0123456789"
    with caplog.at_level(logging.ERROR), pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({"client_key": key, "output_mode": ["text"]})  # falta input_mode
            ws.receive_text()
    assert "handshake" in caplog.text.lower()  # el error sigue registrándose
    assert key[:6] not in caplog.text
    assert "input_mode" in caplog.text


@pytest.mark.parametrize(
    "credentials",
    [{}, {"client_key": VALID_KEY, "admin_token": "test-admin-token"}],
    ids=["none", "both"],
)
def test_handshake_needs_exactly_one_credential(client, caplog, credentials):
    """M19: `client_key` o `admin_token`, una de las dos; el log no lleva ninguna."""
    with caplog.at_level(logging.ERROR), pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({**credentials, "input_mode": "text", "output_mode": ["text"]})
            ws.receive_text()
    assert "value_error" in caplog.text
    assert "test-admin-token" not in caplog.text
    assert VALID_KEY not in caplog.text


@pytest.mark.parametrize("conversation", ["", "x" * 65])
def test_handshake_conversation_is_validated(client, conversation):
    with pytest.raises(Exception), client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"],
             "conversation": conversation}
        )
        ws.receive_text()


# ── Primer frame binario (#232) y ClientGone en el health check (#205) ────────

def _start_server():
    api = FastAPI()
    api.add_middleware(RequestIdMiddleware)
    api.include_router(stream_router)
    config = uvicorn.Config(
        api, host="127.0.0.1", port=0, log_level="warning", loop="asyncio", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn no arrancó")
        time.sleep(0.01)
    return server, thread


@pytest.fixture
def real_port():
    server, thread = _start_server()
    yield server.servers[0].sockets[0].getsockname()[1]
    server.should_exit = True
    thread.join(timeout=10)


async def test_binary_first_frame_closes_4001(real_port, caplog):
    """#232: antes, `receive_text()` lanzaba KeyError('text') sin capturar."""
    caplog.set_level(logging.ERROR)
    async with websockets.connect(f"ws://127.0.0.1:{real_port}/ws/stream") as ws:
        await ws.send(b"\x00\x01\x02\x03")
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await asyncio.wait_for(ws.recv(), timeout=5)
    close = exc.value.rcvd
    assert close is not None
    assert (close.code, close.reason) == (4001, "handshake must be a text frame")
    assert not [r for r in caplog.records if r.exc_info], "no debe haber traceback sin capturar"


def test_binary_first_frame_does_not_raise_under_testclient(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_bytes(b"\x00\x01")
        with pytest.raises(Exception) as exc:
            ws.receive_text()
    assert getattr(exc.value, "code", None) == 4001


def test_client_gone_during_health_check_is_a_clean_disconnect(
    client, mock_orchestrator, monkeypatch, caplog
):
    """#205: ClientGone de notify_service_status(propagate=True) escapaba del endpoint."""
    mock_orchestrator.ping = AsyncMock(return_value=False)

    async def _gone(self, payload):
        raise ClientGone("client left")

    monkeypatch.setattr(QueuedSender, "send_json", _gone)
    caplog.set_level(logging.INFO)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"]})
        with pytest.raises(Exception):
            ws.receive_text()

    assert any(
        r.levelno == logging.INFO and "desconect" in r.getMessage().lower()
        for r in caplog.records
    )
    # La limpieza sigue corriendo.
    assert app.state.client_registry.get(CLIENT_ID) is None
    sessions = app.state.session_registry.get_all()
    assert len(sessions) == 1 and sessions[0].ended_at is not None
