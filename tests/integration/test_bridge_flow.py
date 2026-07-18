"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""
from src.services.protocol import OrchestratorEvent
from tests.integration.conftest import VALID_KEY, CLIENT_ID

HANDSHAKE_TEXT = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text"],
}


def test_text_message_produces_token(client):
    """Cliente manda texto → recibe turn_start then token del orchestrator."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("hola")
        turn_start = ws.receive_json()
        assert turn_start["type"] == "turn_start"
        assert turn_start["turn_id"] == "t-1"
        assert turn_start["turn_seq"] == 1
        msg = ws.receive_json()
        assert msg["type"] == "token"
        assert msg["turn_id"] == "t-1"
        assert msg["text"] == "Hola"


def test_orchestrator_receives_correct_user_id(client, mock_registry, mock_orchestrator):
    """El user_id pasado a stream_response coincide con el client UUID."""
    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
        captured["user_id"] = user_id
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("test")
        ws.receive_json()  # turn_start
        ws.receive_json()  # token

    assert captured["user_id"] == CLIENT_ID
