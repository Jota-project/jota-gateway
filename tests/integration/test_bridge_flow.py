"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""
from sqlmodel import Session
from unittest.mock import AsyncMock, MagicMock

from src.db.models import ClientRecord
from src.services.protocol import OrchestratorEvent
from tests.integration.conftest import VALID_KEY, CLIENT_ID, CLIENT_NAME

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


def _make_client_with(db_engine, mock_services, mock_registry, monkeypatch, **fields):
    """Inserta un ClientRecord con los campos especificados y devuelve un TestClient."""
    from starlette.testclient import TestClient
    from src.main import app
    from src.services.openclaw.registry import TurnRegistry, ClientRegistry

    with Session(db_engine) as s:
        s.add(ClientRecord(
            id=CLIENT_ID,
            name=CLIENT_NAME,
            client_key=VALID_KEY,
            is_active=True,
            **fields,
        ))
        s.commit()

    monkeypatch.setattr("src.main.ReconnectingOpenClawClient", lambda *a, **kw: mock_registry)
    monkeypatch.setattr("src.main.OpenClawClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.FrameDispatcher", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.TurnRegistry", lambda: TurnRegistry())
    monkeypatch.setattr("src.main.ClientRegistry", lambda: ClientRegistry())
    mock_registry.connect = AsyncMock()
    mock_registry.close = AsyncMock()
    return TestClient(app)


def test_system_prompt_extra_included_in_orchestrator_payload(
    db_engine, mock_services, mock_registry, mock_orchestrator, monkeypatch
):
    """system_prompt_extra de ClientConfig se pasa a stream_response."""
    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None, session_key=None):
        captured["system_prompt_extra"] = system_prompt_extra
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    with _make_client_with(db_engine, mock_services, mock_registry, monkeypatch,
                           system_prompt_extra="Habla en inglés") as c:
        with c.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_TEXT)
            ws.receive_json()  # ready
            ws.send_text("test")
            ws.receive_json()  # turn_start
            ws.receive_json()  # token

    assert captured.get("system_prompt_extra") == "Habla en inglés"
