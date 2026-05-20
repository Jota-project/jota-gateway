"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""
import httpx
from tests.integration.conftest import (
    VALID_KEY, CLIENT_UUID, SESSION_RESPONSE,
    make_mock_orchestrator, make_mock_registry,
)
from src.services.orchestrators.protocol import OrchestratorEvent

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


def test_orchestrator_receives_correct_user_id(client, mock_registry, mock_orchestrator):
    """El user_id pasado a stream_response coincide con el client UUID."""
    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None):
        captured["user_id"] = user_id
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()  # consumir token

    assert captured["user_id"] == CLIENT_UUID


def test_preferred_model_id_included_in_orchestrator_payload(
    mock_services, mock_registry, mock_orchestrator, monkeypatch
):
    """preferred_model_id de ClientConfig se pasa como model_id a stream_response."""
    from starlette.testclient import TestClient
    from src.main import app

    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "preferred_model_id": "llama3-70b"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None):
        captured["model_id"] = model_id
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    monkeypatch.setattr("src.main.build_registry", lambda: mock_registry)
    with TestClient(app) as c:
        with c.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_TEXT)
            ws.send_text("test")
            ws.receive_json()

    assert captured.get("model_id") == "llama3-70b"


def test_system_prompt_extra_included_in_orchestrator_payload(
    mock_services, mock_registry, mock_orchestrator, monkeypatch
):
    """system_prompt_extra de ClientConfig se pasa a stream_response."""
    from starlette.testclient import TestClient
    from src.main import app

    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "system_prompt_extra": "Habla en inglés"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None):
        captured["system_prompt_extra"] = system_prompt_extra
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    monkeypatch.setattr("src.main.build_registry", lambda: mock_registry)
    with TestClient(app) as c:
        with c.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_TEXT)
            ws.send_text("test")
            ws.receive_json()

    assert captured.get("system_prompt_extra") == "Habla en inglés"
