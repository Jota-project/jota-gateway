"""Integration: pipeline_event messages appear in WS stream when status in output_mode."""
from tests.integration.conftest import VALID_KEY

HANDSHAKE_WITH_STATUS = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text", "status"],
}
HANDSHAKE_TEXT_ONLY = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text"],
}


def _collect_until_token(ws, max_messages=15):
    """Collect messages until a 'token' type appears, return all collected."""
    messages = []
    for _ in range(max_messages):
        try:
            msg = ws.receive_json()
            messages.append(msg)
            if msg.get("type") == "token":
                break
        except Exception:
            break
    return messages


def test_pipeline_events_forwarded_when_status_in_output_mode(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_WITH_STATUS)
        ws.send_text("hola")
        messages = _collect_until_token(ws)

    types = [m["type"] for m in messages]
    assert "pipeline_event" in types
    assert "token" in types


def test_pipeline_event_has_required_fields(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_WITH_STATUS)
        ws.send_text("hola")
        messages = _collect_until_token(ws)

    pipeline_events = [m for m in messages if m.get("type") == "pipeline_event"]
    assert len(pipeline_events) > 0
    for event in pipeline_events:
        assert "stage" in event
        assert "elapsed_ms" in event
        assert "turn" in event


def test_pipeline_events_not_forwarded_without_status(client):
    """Existing text-only clients must not receive pipeline_event messages."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT_ONLY)
        ws.receive_json()  # ready
        ws.send_text("hola")
        turn_start = ws.receive_json()  # turn_start
        assert turn_start["type"] == "turn_start"
        msg = ws.receive_json()  # token
    assert msg["type"] == "token"


def test_session_start_event_is_first(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_WITH_STATUS)
        ws.send_text("hola")
        messages = _collect_until_token(ws)

    pipeline_events = [m for m in messages if m.get("type") == "pipeline_event"]
    assert pipeline_events[0]["stage"] == "session_start"


def test_llm_start_and_first_token_events_present(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_WITH_STATUS)
        ws.send_text("hola")
        messages = _collect_until_token(ws)

    stages = [m["stage"] for m in messages if m.get("type") == "pipeline_event"]
    assert "llm_start" in stages
    assert "llm_first_token" in stages


def test_session_registered_in_registry_after_connect(client):
    """After a WS session, the session should appear in app.state.session_registry."""
    from src.main import app

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_WITH_STATUS)
        ws.send_text("hola")
        _collect_until_token(ws)

    sessions = app.state.session_registry.get_all()
    assert len(sessions) == 1
