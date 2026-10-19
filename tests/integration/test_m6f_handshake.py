"""M6f: #232 (primer frame binario del handshake) y #205 (ClientGone en health_check)."""

import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock

import pytest
import uvicorn
import websockets
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.core.request_id import RequestIdMiddleware
from src.main import app
from src.services.outbound import ClientGone, QueuedSender
from tests.integration.conftest import CLIENT_ID, VALID_KEY


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


async def test_binary_first_frame_closes_1008_with_stable_reason(real_port, caplog):
    """#232: antes, `receive_text()` lanzaba KeyError('text') sin capturar."""
    caplog.set_level(logging.ERROR)
    async with websockets.connect(f"ws://127.0.0.1:{real_port}/ws/stream") as ws:
        await ws.send(b"\x00\x01\x02\x03")
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await asyncio.wait_for(ws.recv(), timeout=5)
    close = exc.value.rcvd
    assert close is not None
    assert (close.code, close.reason) == (1008, "Handshake invalido. Se esperaba texto.")
    assert not [r for r in caplog.records if r.exc_info], "no debe haber traceback sin capturar"


def test_binary_first_frame_does_not_raise_under_testclient(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_bytes(b"\x00\x01")
        with pytest.raises(Exception) as exc:
            ws.receive_text()
    assert getattr(exc.value, "code", None) == 1008


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
