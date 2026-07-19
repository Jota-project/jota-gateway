"""Issue #101: pre-`ready` WebSocket failure paths must not leak transcriber,
bridge, or session state.

Drives `gateway_websocket()` directly against a fake WebSocket instead of a
real ASGI transport: the pre-fix code path (ready-send failure) never called
`websocket.close()`, so a real client-side blocking `receive()` against it
hangs forever — exercising it through a fake object sidesteps that entirely
and lets us assert on cleanup deterministically.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api import routes
from src.models.schemas import Client, ClientConfig
from src.services.openclaw.registry import ClientRegistry
from src.services.session_registry import SessionRegistry

CLIENT_KEY = "test-key"
CLIENT_ID = "hab_sito"

HANDSHAKE_TEXT = {
    "client_key": CLIENT_KEY,
    "input_mode": "text",
    "output_mode": ["text"],
}


class FakeWebSocket:
    """Minimal stand-in for fastapi.WebSocket, only what routes.py touches."""

    def __init__(self, handshake_msg, app_state, send_json_raises_on=None):
        self._handshake_msg = handshake_msg
        self.scope = {"app": SimpleNamespace(state=app_state)}
        # RequestIdMiddleware (issue #106) assigns scope["state"]["request_id"]
        # for every real connection; routes.py reads it via websocket.state.
        # The middleware is bypassed here (route driven directly), so model it.
        self.state = SimpleNamespace(request_id="test-request-id")
        self._send_json_raises_on = send_json_raises_on
        self.sent: list[dict] = []
        self.closed_with = None
        self.client_state = SimpleNamespace(name="CONNECTED")

    async def accept(self):
        pass

    async def receive_text(self):
        return json.dumps(self._handshake_msg)

    async def send_json(self, data):
        self.sent.append(data)
        if self._send_json_raises_on and data.get("type") == self._send_json_raises_on:
            raise RuntimeError("client gone")

    async def close(self, code=1000, reason=""):
        self.closed_with = (code, reason)
        self.client_state = SimpleNamespace(name="DISCONNECTED")

    async def receive(self):
        return {"type": "websocket.disconnect"}


def _make_app_state(orchestrator_ping_result=True):
    orchestrator = AsyncMock()
    orchestrator.ping = AsyncMock(return_value=orchestrator_ping_result)
    orchestrator.gateway_info = None
    return SimpleNamespace(
        openclaw=orchestrator,
        tts=AsyncMock(),
        client_registry=ClientRegistry(),
        session_registry=SessionRegistry(),
    ), orchestrator


@pytest.fixture(autouse=True)
def _patch_db_client(monkeypatch):
    async def _get_session(client_key):
        assert client_key == CLIENT_KEY
        return (
            Client(id=CLIENT_ID, client_key=CLIENT_KEY, is_active=True),
            ClientConfig(),
        )

    monkeypatch.setattr(routes.db_client, "get_session", _get_session)


async def test_ready_send_failure_leaves_no_zombie_bridge():
    app_state, _ = _make_app_state(orchestrator_ping_result=True)
    ws = FakeWebSocket(HANDSHAKE_TEXT, app_state, send_json_raises_on="ready")

    await routes.gateway_websocket(ws)

    assert app_state.client_registry.get(CLIENT_ID) is None
    sessions = app_state.session_registry.get_all()
    assert len(sessions) == 1
    assert sessions[0].status == "error"
    assert sessions[0].ended_at is not None


async def test_connect_internal_services_failure_leaves_no_zombie_session(monkeypatch):
    """connect_internal_services() raising is near-unreachable in practice
    (ReconnectingTranscriberClient.connect() never raises — see CLAUDE.md),
    but the try/finally must still cover it defensively per the AC."""
    from src.services.bridge import JotaBridge

    monkeypatch.setattr(
        JotaBridge, "connect_internal_services",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    app_state, _ = _make_app_state(orchestrator_ping_result=True)
    ws = FakeWebSocket(HANDSHAKE_TEXT, app_state)

    await routes.gateway_websocket(ws)

    assert app_state.client_registry.get(CLIENT_ID) is None
    sessions = app_state.session_registry.get_all()
    assert len(sessions) == 1
    assert sessions[0].status == "error"
    assert sessions[0].ended_at is not None
    assert ws.closed_with == (1011, "Problema estableciendo microservicios internos del hub.")
