"""Tests for JotaBridge.health_check()."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def make_bridge():
    """Factory: returns a bridge with mocked ws, orchestrator, and transcriber."""
    def _make(input_mode="audio", output_mode=None):
        if output_mode is None:
            output_mode = ["audio", "text", "status"]
        ws = AsyncMock()
        bridge = JotaBridge(client_id="test", client_ws=ws)
        bridge.handshake = Handshake(input_mode=input_mode, output_mode=output_mode)
        bridge.orchestrator = AsyncMock()
        bridge.orchestrator.ping = AsyncMock(return_value=True)
        bridge.transcriber = MagicMock()
        bridge.transcriber._is_ready = True
        return bridge
    return _make


# --- Orchestrator checks ---

async def test_health_check_passes_when_all_ok(make_bridge):
    bridge = make_bridge()

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is True
    bridge.client_ws.send_json.assert_not_called()


async def test_health_check_fails_when_orchestrator_down(make_bridge):
    bridge = make_bridge()
    bridge.orchestrator.ping = AsyncMock(return_value=False)

    result = await bridge.health_check()

    assert result is False
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "orchestrator",
        "status": "unavailable",
        "message": "Orchestrator unavailable, closing session",
    })


# --- Transcriber checks ---

async def test_health_check_fails_when_transcriber_not_ready(make_bridge):
    bridge = make_bridge(input_mode="audio")
    bridge.transcriber._is_ready = False

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is False
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "transcriber",
        "status": "unavailable",
        "message": "Transcriber unavailable, closing session",
    })


async def test_health_check_fails_when_transcriber_is_none(make_bridge):
    """transcriber=None with audio input must fail."""
    bridge = make_bridge(input_mode="audio")
    bridge.transcriber = None

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is False
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "transcriber",
        "status": "unavailable",
        "message": "Transcriber unavailable, closing session",
    })


async def test_health_check_skips_transcriber_check_for_text_input(make_bridge):
    """If input_mode is text, transcriber state is irrelevant."""
    bridge = make_bridge(input_mode="text")
    bridge.transcriber._is_ready = False  # would fail if checked

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is True


# --- TTS checks ---

async def test_health_check_warns_but_continues_when_tts_down(make_bridge):
    bridge = make_bridge(output_mode=["audio", "text"])

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=False)):
        result = await bridge.health_check()

    assert result is True  # session continues
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "tts",
        "status": "unavailable",
        "message": "Audio output unavailable",
    })


async def test_health_check_skips_tts_check_when_no_audio_output(make_bridge):
    """If output_mode has no 'audio', TTS is not pinged."""
    bridge = make_bridge(output_mode=["text", "status"])

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=False)) as mock_ping:
        result = await bridge.health_check()

    assert result is True
    mock_ping.assert_not_called()
