"""Tests for JotaBridge.health_check() — four paths:
all ok, orchestrator unavailable, transcriber not ready, TTS degraded.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.pipeline_tracker import PipelineTracker, _NullWS

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _make_bridge(input_mode="text", output_mode=None):
    if output_mode is None:
        output_mode = ["text"]
    ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:hc", client_id="hab_sito",
        input_mode=input_mode, output_mode=output_mode,
        client_ws=_NullWS(), registry=registry,
    )
    handshake = Handshake(client_key="test-key", input_mode=input_mode, output_mode=output_mode)
    orch = AsyncMock()
    orch.ping = AsyncMock(return_value=True)
    bridge = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws,
                        orchestrator=orch, tracker=tracker, handshake=handshake)
    return bridge, ws, orch


async def test_health_check_returns_true_when_all_ok():
    bridge, ws, orch = _make_bridge()
    result = await bridge.health_check()
    assert result is True


async def test_health_check_returns_false_when_orchestrator_unavailable():
    bridge, ws, orch = _make_bridge()
    orch.ping = AsyncMock(return_value=False)
    result = await bridge.health_check()
    assert result is False
    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["service"] == "orchestrator"
    assert payload["status"] == "unavailable"


async def test_health_check_returns_false_when_transcriber_not_ready():
    bridge, ws, orch = _make_bridge(input_mode="audio")
    bridge.transcriber = MagicMock()
    bridge.transcriber._is_ready = False
    result = await bridge.health_check()
    assert result is False
    payload = ws.send_json.call_args[0][0]
    assert payload["service"] == "transcriber"
    assert payload["status"] == "unavailable"


async def test_health_check_tts_unavailable_still_returns_true():
    """TTS unavailable is degraded — session continues."""
    bridge, ws, orch = _make_bridge(output_mode=["audio", "text"])
    with patch("src.services.tts_client.TTSClient.ping", new=AsyncMock(return_value=False)):
        result = await bridge.health_check()
    assert result is True
    payload = ws.send_json.call_args[0][0]
    assert payload["service"] == "tts"
    assert payload["status"] == "unavailable"
