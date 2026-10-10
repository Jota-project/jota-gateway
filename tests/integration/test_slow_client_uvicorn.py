"""Integration: slow client against a REAL uvicorn server (issue #130 S2).

The mini app is a FastAPI app with the SAME middlewares, in the SAME order, as src/main.py
(RequestIdMiddleware, then TransportCaptureMiddleware) — a plain Starlette app would not
prove the middleware sees uvicorn's raw `send` in the real stack. The endpoint mirrors
what routes.py does: a QueuedSender whose on_slow calls drop_slow_client. The client is a
raw socket so it can stop reading while the server keeps writing.
"""

import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket

from src.core import transport as transport_mod
from src.core.config import settings
from src.core.request_id import RequestIdMiddleware
from src.core.transport import TransportCaptureMiddleware, drop_slow_client
from src.services.outbound import QueuedSender

CHUNK = b"\x00" * (256 * 1024)
CHUNKS = 128  # 32 MiB: far more than socket buffers can absorb

REQUEST = (
    b"GET /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
)


class Probe:
    def __init__(self):
        self.slow_called = threading.Event()
        self.disconnected = threading.Event()
        self.slow_calls = 0
        self.had_transport: bool | None = None


def _make_app(probe: Probe) -> FastAPI:
    app = FastAPI()
    # Same registration order as src/main.py.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TransportCaptureMiddleware)

    @app.websocket("/ws")
    async def endpoint(websocket: WebSocket):
        await websocket.accept()
        # Direct diagnosis: if this is False the middleware did not see uvicorn's raw
        # `send` and the drop below cannot abort (it would only hang for the grace).
        probe.had_transport = websocket.scope["state"].get("transport") is not None

        async def on_slow():
            probe.slow_calls += 1
            probe.slow_called.set()
            await drop_slow_client(websocket)

        sender = QueuedSender(websocket, on_slow=on_slow)
        for _ in range(CHUNKS):
            await sender.send_bytes(CHUNK)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        finally:
            probe.disconnected.set()
            await sender.aclose(1.0)

    return app


@pytest.fixture
def probe():
    return Probe()


@pytest.fixture
def live_port(probe):
    config = uvicorn.Config(
        _make_app(probe), host="127.0.0.1", port=0, log_level="warning", loop="asyncio",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn no arrancó")
            time.sleep(0.01)
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive(), "uvicorn no se detuvo"


def _connect(port: int) -> socket.socket:
    sock = socket.socket()
    # A tiny receive buffer so the kernel absorbs as little as possible.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect(("127.0.0.1", port))
    sock.sendall(REQUEST)
    sock.settimeout(10)
    buf = b""
    while b"\r\n\r\n" not in buf:  # byte by byte: never consume a frame
        byte = sock.recv(1)
        assert byte, f"el servidor cerró durante el handshake: {buf[:60]!r}"
        buf += byte
    assert buf.startswith(b"HTTP/1.1 101"), buf[:60]
    sock.settimeout(None)
    return sock


def test_client_that_stops_reading_is_dropped(live_port, probe, monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 1.0)
    monkeypatch.setattr(transport_mod, "SLOW_CLOSE_GRACE_S", 0.5)
    sock = _connect(live_port)
    try:
        assert probe.slow_called.wait(timeout=20), "el servidor nunca declaró lento al cliente"
        assert probe.had_transport is True, (
            "TransportCaptureMiddleware no capturó el transporte: no recibió el send crudo "
            "de uvicorn en la pila FastAPI"
        )
        assert probe.disconnected.wait(timeout=10), "la sesión no terminó tras el abort"
        assert probe.slow_calls == 1

        sock.settimeout(10)
        while True:  # the connection really ends for the client
            try:
                data = sock.recv(65536)
            except TimeoutError:
                pytest.fail("el servidor no cerró la conexión del cliente lento tras el abort")
            except ConnectionResetError:
                break  # RST after abort(): also an end of connection
            if not data:
                break
    finally:
        sock.close()


def test_client_that_reads_everything_is_not_dropped(live_port, probe, monkeypatch):
    """Review focus 5: the control — a healthy client must never be cut."""
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 1.0)
    sock = _connect(live_port)
    try:
        sock.settimeout(10)
        total = 0
        while total < CHUNKS * len(CHUNK):
            data = sock.recv(1 << 20)
            assert data, "el servidor cerró a un cliente que sí leía"
            total += len(data)
    finally:
        sock.close()
    assert not probe.slow_called.is_set()
    assert probe.slow_calls == 0
