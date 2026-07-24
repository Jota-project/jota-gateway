"""Tests for JotaBridge.health_check() snapshot."""
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.pipeline_tracker import PipelineTracker, _NullWS
from src.services.reconnection import ConnectionState

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
                        orchestrator=orch, tts=AsyncMock(), tracker=tracker, handshake=handshake,
                        client_registry=ClientRegistry(), default_agent="main")
    return bridge, ws, orch


async def test_health_check_returns_full_snapshot_when_audio_and_tts_ok():
    bridge, ws, orch = _make_bridge(input_mode="audio", output_mode=["audio", "text"])
    bridge.transcriber = MagicMock()
    bridge.transcriber.state = ConnectionState.CONNECTED

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result == {"barge_in": True, "tts": True, "transcriber": True}


async def test_health_check_returns_tts_false_when_ping_fails():
    bridge, ws, orch = _make_bridge(input_mode="audio", output_mode=["audio", "text"])
    bridge.transcriber = MagicMock()
    bridge.transcriber.state = ConnectionState.CONNECTED

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=False)):
        result = await bridge.health_check()

    assert result == {"barge_in": True, "tts": False, "transcriber": True}
    status_calls = [c.args[0] for c in ws.send_json.call_args_list]
    assert any(
        call["type"] == "status" and call["service"] == "tts" and call["state"] == "unavailable"
        for call in status_calls
    )


async def test_health_check_returns_transcriber_false_when_degraded():
    bridge, ws, orch = _make_bridge(input_mode="audio", output_mode=["audio", "text"])
    bridge.transcriber = MagicMock()
    bridge.transcriber.state = ConnectionState.DEGRADED

    result = await bridge.health_check()

    assert result == {"barge_in": False, "tts": False, "transcriber": False}
    status_calls = [c.args[0] for c in ws.send_json.call_args_list]
    assert any(
        call["type"] == "status" and call["service"] == "transcriber" and call["state"] == "unavailable"
        for call in status_calls
    )


async def test_health_check_returns_all_false_for_text_only_handshake():
    bridge, ws, orch = _make_bridge(input_mode="text", output_mode=["text"])
    bridge.transcriber = None

    result = await bridge.health_check()

    assert result == {"barge_in": False, "tts": False, "transcriber": False}


async def test_health_check_returns_none_when_orchestrator_unavailable():
    bridge, ws, orch = _make_bridge()
    orch.ping = AsyncMock(return_value=False)

    result = await bridge.health_check()

    assert result is None
    payload = ws.send_json.call_args_list[0].args[0]
    assert payload == {"type": "status", "service": "orchestrator", "state": "unavailable"}
