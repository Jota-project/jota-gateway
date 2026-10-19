"""#216 (parte de G-33): tope al primer mensaje del WS, contra un uvicorn REAL.

El handshake se parsea (json.loads + pydantic) ANTES de autenticar, así que cualquiera puede
pedirnos ese trabajo; un mensaje enorme cuesta ~8x su tamaño en memoria. Dos topes:
`HANDSHAKE_MAX_BYTES` (aquí, antes de parsear) y `--ws-max-size` de uvicorn (en el CMD de
la imagen, antes de que el mensaje llegue a la app).
"""

import asyncio
import json
import threading
import time

import pytest
import uvicorn
import websockets
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.core.config import Settings, settings
from src.core.request_id import RequestIdMiddleware


def _start(**uvicorn_kwargs):
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.include_router(stream_router)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", loop="asyncio", lifespan="off",
        **uvicorn_kwargs,
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
def make_server():
    started = []

    def _make(**kw):
        server, thread = _start(**kw)
        started.append((server, thread))
        return server.servers[0].sockets[0].getsockname()[1]

    yield _make
    for server, thread in started:
        server.should_exit = True
        thread.join(timeout=10)


async def _first_close(port, payload):
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws/stream", max_size=None) as ws:
        await ws.send(payload)
        try:
            await asyncio.wait_for(ws.recv(), timeout=5)
        except websockets.ConnectionClosed as exc:
            return exc.rcvd


async def test_oversized_valid_handshake_is_rejected_before_parsing(make_server, monkeypatch):
    monkeypatch.setattr(settings, "HANDSHAKE_MAX_BYTES", 4096)
    port = make_server()
    payload = json.dumps(
        {"client_key": "k", "input_mode": "text", "output_mode": ["text"], "agent": "a" * 10_000}
    )
    close = await _first_close(port, payload)
    assert close is not None
    assert (close.code, close.reason) == (1008, "Handshake too large")


async def test_ws_max_size_closes_with_1009(make_server):
    port = make_server(ws_max_size=64 * 1024)
    close = await _first_close(port, "x" * (256 * 1024))
    assert close is not None and close.code == 1009


def test_handshake_defaults():
    # Default de HANDSHAKE_TIMEOUT_S bajado de 10 a 5 s (#216); documentado en CLAUDE.md.
    assert Settings.model_fields["HANDSHAKE_TIMEOUT_S"].default == 5.0
    assert Settings.model_fields["HANDSHAKE_MAX_BYTES"].default == 4096
