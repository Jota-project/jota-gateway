"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""

import pytest

from jota_gateway.core.config import settings
from jota_gateway.services.protocol import OrchestratorEvent
from tests.integration.conftest import CLIENT_ID, VALID_KEY

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


def test_orchestrator_session_key_carries_client_id(client, mock_registry, mock_orchestrator):
    """La session_key pasada a stream_response termina en el UUID del cliente."""
    captured = {}

    async def _stream(text, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("test")
        ws.receive_json()  # turn_start
        ws.receive_json()  # token

    assert captured["session_key"].endswith(f":{CLIENT_ID}")


@pytest.fixture
def voice_off(monkeypatch):
    """STT y TTS en `off`; debe ir antes que `client`, que construye los proveedores al arrancar."""
    monkeypatch.setattr(settings, "STT_PROVIDER", "off")
    monkeypatch.setattr(settings, "TTS_PROVIDER", "off")


def _receive_until(ws, kind: str) -> list[dict]:
    msgs = [ws.receive_json()]
    while msgs[-1]["type"] != kind:
        msgs.append(ws.receive_json())
    return msgs


def test_session_with_voice_off_stays_open_in_text(voice_off, client):
    """Voz `off`: la sesión de voz arranca degradada como con los servicios caídos y sigue en texto."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": VALID_KEY, "input_mode": "audio", "output_mode": ["text", "audio"]})
        *before, ready = _receive_until(ws, "ready")
        assert ready["live_capabilities"]["transcriber"] is False
        assert ready["live_capabilities"]["tts"] is False
        # El mismo aviso que con un servicio caído: ningún valor nuevo de `status`.
        assert sorted((m["type"], m["service"], m["state"]) for m in before) == [
            ("status", "transcriber", "unavailable"),
            ("status", "tts", "unavailable"),
        ]
        ws.send_bytes(b"\x00" * 1280)  # el audio se descarta sin cerrar nada
        ws.send_json({"type": "end"})
        for turn_id in ("t-1", "t-2"):  # el segundo turno prueba que la sesión sigue abierta
            ws.send_json({"type": "send", "text": "hola"})
            msgs = _receive_until(ws, "turn_end")
            assert [m["type"] for m in msgs] == ["turn_start", "token", "turn_end"]
            assert msgs[-1]["turn_id"] == turn_id
