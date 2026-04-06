"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""
import json
import httpx
from tests.integration.conftest import VALID_KEY, CLIENT_UUID, SESSION_RESPONSE

HANDSHAKE_TEXT = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text"],
}


def test_text_message_produces_token(client):
    """Cliente manda texto → recibe token del orchestrator."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("hola")
        msg = ws.receive_json()
        assert msg["type"] == "token"
        assert msg["content"] == "Hola"


def test_orchestrator_receives_correct_headers(client, mock_services):
    """El request al orchestrator incluye x-client-key y x-client-id correctos."""
    captured = {}

    def capture(req):
        captured["x-client-key"] = req.headers.get("x-client-key")
        captured["x-client-id"] = req.headers.get("x-client-id")
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()  # consumir token

    assert captured["x-client-key"] == VALID_KEY
    assert captured["x-client-id"] == CLIENT_UUID


def test_preferred_model_id_included_in_orchestrator_payload(client, mock_services):
    """preferred_model_id de ClientConfig se envía en el body al orchestrator."""
    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "preferred_model_id": "llama3-70b"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured_body = {}

    def capture(req):
        captured_body.update(json.loads(req.content))
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()

    assert captured_body.get("model_id") == "llama3-70b"


def test_system_prompt_extra_included_in_orchestrator_payload(client, mock_services):
    """system_prompt_extra de ClientConfig se envía en el body al orchestrator."""
    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "system_prompt_extra": "Habla en inglés"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured_body = {}

    def capture(req):
        captured_body.update(json.loads(req.content))
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()

    assert captured_body.get("system_prompt_extra") == "Habla en inglés"
