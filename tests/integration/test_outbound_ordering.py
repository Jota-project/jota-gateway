"""Integration: ordered outbound path and drain-on-close (issue #130 S1)."""

from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.integration.conftest import VALID_KEY

HANDSHAKE = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text", "status"],
}


def _drain_until_turn_end(ws, limit=40):
    msgs = []
    for _ in range(limit):
        m = ws.receive_json()
        msgs.append(m)
        if m.get("type") == "turn_end":
            break
    return msgs


def test_ready_precedes_turn_and_turn_start_precedes_tokens(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        ws.send_text("hola")
        msgs = _drain_until_turn_end(ws)

    types = [m["type"] for m in msgs]
    assert "ready" in types
    assert types.index("ready") < types.index("turn_start") < types.index("token")
    assert types[-1] == "turn_end"
    first_turn_event = types.index("turn_start")
    assert not [
        i
        for i, m in enumerate(msgs)
        if m["type"] == "pipeline_event" and m["stage"] == "llm_start" and i < first_turn_event
    ]


def test_health_check_failure_delivers_status_then_closes_1011(client, mock_orchestrator):
    mock_orchestrator.ping = AsyncMock(return_value=False)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        status = ws.receive_json()
        assert status == {"type": "status", "service": "orchestrator", "state": "unavailable"}
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1011


def test_rejected_handshake_creates_no_writer_task(client, monkeypatch):
    created = []
    from src.api import routes

    real = routes.QueuedSender

    def spy(*a, **kw):
        created.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(routes, "QueuedSender", spy)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": "no-existe", "input_mode": "text", "output_mode": ["text"]})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008
    assert created == []
