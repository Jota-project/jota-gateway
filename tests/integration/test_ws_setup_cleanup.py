"""Issue #101: pre-`ready` WebSocket failure paths must not leak transcriber,
bridge, or session state.

Before the fix, `health_check()`/ready-send failures in routes.py returned
early without calling `bridge.close_all()` or `tracker.close()` — the bridge
stayed registered in `ClientRegistry` forever, and the session stayed
"active" forever in `SessionRegistry` (which never evicts active entries).
"""

from unittest.mock import AsyncMock

import pytest

from src.main import app
from tests.integration.conftest import CLIENT_ID, VALID_KEY

HANDSHAKE_TEXT = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text"],
}


def _the_only_session():
    sessions = app.state.session_registry.get_all()
    assert len(sessions) == 1, f"expected exactly one session, got {len(sessions)}"
    return sessions[0]


def test_orchestrator_down_during_handshake_leaves_no_zombie_bridge(client, mock_orchestrator):
    """Health check fails (orchestrator unreachable) → WS closes 1011, and
    the bridge/session must not be left registered forever."""
    mock_orchestrator.ping = AsyncMock(return_value=False)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        status_msg = ws.receive_json()  # health_check() notifies before closing
        assert status_msg["type"] == "status"
        assert status_msg["service"] == "orchestrator"
        assert status_msg["state"] == "unavailable"
        with pytest.raises(Exception):
            ws.receive_text()  # the WS close frame

    assert app.state.client_registry.get(CLIENT_ID) is None
    session = _the_only_session()
    assert session.status == "error"
    assert session.ended_at is not None
