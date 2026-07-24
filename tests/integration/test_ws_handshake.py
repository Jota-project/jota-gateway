"""Tests para WebSocket handshake (/ws/stream)."""
import logging
import re
from uuid import UUID

import pytest

from src.core.config import settings
from src.core.logging import fingerprint_key
from tests.integration.conftest import CLIENT_ID, VALID_KEY


def _assert_request_id(message: str) -> None:
    match = re.search(r"\brequest_id=([0-9a-f-]{36})\b", message)
    assert match is not None
    assert UUID(match.group(1)).version == 4


def test_malformed_json_closes_ws(client):
    """JSON malformado como primer mensaje → WS se cierra (código 1008)."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text("not-json{{")
            ws.receive_text()  # debe lanzar excepción al recibir close frame


def test_invalid_client_key_log_is_safe_and_correlatable(client, caplog, monkeypatch):
    key = "bad-key\nforged-log-entry"
    source_ip = "198.51.100.23"
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1")

    # Use a mock to capture the warning call since caplog isn't capturing
    import unittest.mock as mock
    warning_calls = []

    def mock_warning(msg, *args, **kwargs):
        warning_calls.append(msg if isinstance(msg, str) else msg % args)

    with mock.patch.object(logging.getLogger("src.api.routes"), "warning", mock_warning):
        ws = client.websocket_connect(
            "/ws/stream",
            headers={"x-real-ip": source_ip},
        )
        ws.__enter__()
        try:
            ws.send_json({
                "client_key": key,
                "input_mode": "text",
                "output_mode": ["text"],
            })
            ws.receive_text()
        except Exception:
            pass
        finally:
            ws.__exit__(None, None, None)

    handshake_msgs = [m for m in warning_calls if "Handshake rechazado" in m]
    assert len(handshake_msgs) == 1
    message = handshake_msgs[0]
    assert f"source_ip={source_ip}" in message
    assert f"fp={fingerprint_key(key)}" in message
    _assert_request_id(message)
    assert key not in message
    assert "bad-key" not in message
    assert "forged-log-entry" not in message
    assert "\n" not in message


def test_missing_required_handshake_field_closes_ws(client):
    """Handshake sin campo requerido (input_mode) → WS se cierra."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({"client_key": VALID_KEY, "output_mode": ["text"]})
            ws.receive_text()


def test_valid_text_mode_handshake_connection_stays_open(client, caplog):
    """Handshake válido — gateway responde con ready y la conexión permanece abierta."""
    import unittest.mock as mock
    info_calls = []

    def mock_info(msg, *args, **kwargs):
        info_calls.append(msg if isinstance(msg, str) else msg % args)

    with mock.patch.object(logging.getLogger("src.api.routes"), "info", mock_info):
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
            assert "requested_capabilities" in ready
            ws.send_text('{"type":"end"}')

    handshake_msgs = [m for m in info_calls if "Handshake verificado" in m]
    assert len(handshake_msgs) == 1
    message = handshake_msgs[0]
    assert f"client_id={CLIENT_ID}" in message
    assert f"fp={fingerprint_key(VALID_KEY)}" in message
    _assert_request_id(message)
    assert VALID_KEY not in message

    # Also verify the full caplog text doesn't contain the key
    assert VALID_KEY not in caplog.text


def test_ready_carries_requested_and_live_capabilities_when_audio(client, monkeypatch):
    """#114 — `ready` divide capabilities en requested/live."""
    from unittest.mock import AsyncMock
    from src.services.reconnection import ConnectionState

    # TTS ping returns False (degraded), transcriber state is CONNECTED
    monkeypatch.setattr(
        "src.services.bridge.TTSClient.ping",
        AsyncMock(return_value=False),
    )
    # Mock transcriber state directly on the bridge after it's created
    # by patching the ReconnectingTranscriberClient class to report CONNECTED
    class FakeTranscriber:
        state = ConnectionState.CONNECTED

        async def connect(self, *, language: str, vad_thold: float, **kw):
            pass

        async def run(self):
            pass

        async def close(self):
            pass

    def fake_transcriber_factory(*a, **kw):
        return FakeTranscriber()

    monkeypatch.setattr(
        "src.services.bridge.ReconnectingTranscriberClient",
        fake_transcriber_factory,
    )

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({
            "client_key": VALID_KEY,
            "input_mode": "audio",
            "output_mode": ["audio", "text"],
        })
        frames = []
        for _ in range(10):
            try:
                frames.append(ws.receive_json())
            except Exception:
                break
            if any(f.get("type") == "ready" for f in frames):
                break
        ready = next(f for f in frames if f.get("type") == "ready")

    assert "capabilities" not in ready
    assert ready["requested_capabilities"] == {
        "barge_in": True,
        "tts": True,
        "transcriber": True,
    }
    assert ready["live_capabilities"] == {
        "barge_in": True,
        "tts": False,
        "transcriber": True,
    }
    status_before_ready = [
        f for f in frames
        if f.get("type") == "status" and f.get("service") == "tts"
    ]
    assert status_before_ready, "Debe haber un status tts unavailable antes de ready"


def test_ready_uses_both_sections_for_text_only_handshake(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({
            "client_key": VALID_KEY,
            "input_mode": "text",
            "output_mode": ["text"],
        })
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert ready["requested_capabilities"] == {
        "barge_in": True,
        "tts": False,
        "transcriber": False,
    }
    assert ready["live_capabilities"] == {
        "barge_in": False,
        "tts": False,
        "transcriber": False,
    }
