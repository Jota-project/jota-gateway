"""Integration: slow-client policy wired through routes.py (issue #130 S2)."""

import asyncio
import time

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.api import routes
from src.core.config import settings
from tests.integration.conftest import CLIENT_ID, VALID_KEY

HANDSHAKE = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text", "status"],
}


def _hang_on_turn_end(monkeypatch):
    """Make the writer's send of `turn_end` block, as a client that stopped reading would."""
    real_send_json = WebSocket.send_json

    async def hanging_send_json(self, data, mode="text"):
        if isinstance(data, dict) and data.get("type") == "turn_end":
            await asyncio.Event().wait()
        await real_send_json(self, data, mode)

    monkeypatch.setattr(WebSocket, "send_json", hanging_send_json)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_slow_client_policy_records_the_event_and_calls_drop_slow_client(client, monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)
    dropped: list = []
    recorded: list = []

    async def fake_drop(websocket, *, record=None):
        assert record is not None
        event = await record()
        recorded.append((event.stage, event.meta))
        dropped.append(websocket)

    monkeypatch.setattr(routes, "drop_slow_client", fake_drop)
    _hang_on_turn_end(monkeypatch)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        ws.send_text("hola")
        assert _wait_for(lambda: len(dropped) == 1), "on_slow no se invocó"

    assert recorded == [("client_slow", {"timeout_s": 0.05})]


def test_slow_client_without_transport_is_closed_with_1013(client, monkeypatch):
    """TestClient has no uvicorn transport: the real drop_slow_client still closes 1013."""
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)
    _hang_on_turn_end(monkeypatch)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        ws.send_text("hola")
        with pytest.raises(WebSocketDisconnect) as exc:
            for _ in range(60):
                ws.receive_json()
    assert exc.value.code == 1013
    # Review focus 8: the real session tears down after the drop (nothing hangs and
    # the bridge leaves the registry), not just the client-visible close code.
    from src.main import app

    assert _wait_for(lambda: app.state.client_registry.get(CLIENT_ID) is None), (
        "el bridge sigue registrado tras cerrar al cliente lento"
    )
