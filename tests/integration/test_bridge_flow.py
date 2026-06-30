"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""
import httpx
from tests.integration.conftest import (
    VALID_KEY, CLIENT_ID, SESSION_RESPONSE, DB_BASE,
)
from src.services.protocol import OrchestratorEvent

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

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None, session_key=None):
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
    mock_services.get(f"{DB_BASE}/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None, session_key=None):
        captured["model_id"] = model_id
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    monkeypatch.setattr("src.main.ReconnectingOpenClawClient", lambda *a, **kw: mock_registry)
    monkeypatch.setattr("src.main.OpenClawClient", lambda *a, **kw: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())
    monkeypatch.setattr("src.main.FrameDispatcher", lambda *a, **kw: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())
    mock_registry.connect = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    mock_registry.close = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    with TestClient(app) as c:
        with c.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_TEXT)
            ws.receive_json()  # ready
            ws.send_text("test")
            ws.receive_json()  # turn_start
            ws.receive_json()  # token

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
    mock_services.get(f"{DB_BASE}/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None, session_key=None):
        captured["system_prompt_extra"] = system_prompt_extra
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    monkeypatch.setattr("src.main.ReconnectingOpenClawClient", lambda *a, **kw: mock_registry)
    monkeypatch.setattr("src.main.OpenClawClient", lambda *a, **kw: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())
    monkeypatch.setattr("src.main.FrameDispatcher", lambda *a, **kw: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())
    mock_registry.connect = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    mock_registry.close = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    with TestClient(app) as c:
        with c.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_TEXT)
            ws.receive_json()  # ready
            ws.send_text("test")
            ws.receive_json()  # turn_start
            ws.receive_json()  # token

    assert captured.get("system_prompt_extra") == "Habla en inglés"
