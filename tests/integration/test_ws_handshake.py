"""Tests para WebSocket handshake (/ws/stream)."""
import pytest

from tests.integration.conftest import VALID_KEY


def test_malformed_json_closes_ws(client):
    """JSON malformado como primer mensaje → WS se cierra (código 1008)."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text("not-json{{")
            ws.receive_text()  # debe lanzar excepción al recibir close frame


def test_invalid_client_key_closes_ws(client, mock_services):
    """Key rechazada por jota-db → WS se cierra."""
    import httpx
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid key"})
    )
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({
                "client_key": "bad-key",
                "input_mode": "text",
                "output_mode": ["text"],
            })
            ws.receive_text()


def test_missing_required_handshake_field_closes_ws(client):
    """Handshake sin campo requerido (input_mode) → WS se cierra."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({"client_key": VALID_KEY, "output_mode": ["text"]})
            ws.receive_text()


def test_valid_text_mode_handshake_connection_stays_open(client):
    """Handshake válido — gateway responde con ready y la conexión permanece abierta."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({
            "client_key": VALID_KEY,
            "input_mode": "text",
            "output_mode": ["text"],
        })
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["input_mode"] == "text"
        assert ready["output_mode"] == ["text"]
        assert "session_id" in ready
        assert "agent" in ready
        assert "capabilities" in ready
        ws.send_text('{"type":"end"}')
