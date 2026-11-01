"""Tests for JotaBridge.health_check() snapshot."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.pipeline_tracker import PipelineTracker
from src.services.reconnection import ConnectionState
from tests.fakes.bridge import sent


@pytest.fixture
def make_hc_bridge(make_bridge):
    def _make(input_mode="text", output_mode=("text",)):
        tracker = PipelineTracker(
            session_id="test:hc",
            client_id="hab_sito",
            input_mode=input_mode,
            output_mode=list(output_mode),
            registry=MagicMock(),
        )
        orch = AsyncMock()
        orch.ping = AsyncMock(return_value=True)
        bridge, ws = make_bridge(
            client_id="hab_sito",
            input_mode=input_mode,
            output_mode=output_mode,
            tracker=tracker,
            orchestrator=orch,
        )
        return bridge, ws, orch

    return _make


async def test_health_check_returns_full_snapshot_when_audio_and_tts_ok(make_hc_bridge):
    bridge, ws, orch = make_hc_bridge(input_mode="audio", output_mode=["audio", "text"])
    bridge.transcriber = MagicMock()
    bridge.transcriber.state = ConnectionState.CONNECTED

    bridge.tts.probe = AsyncMock(return_value=True)
    result = await bridge.health_check()

    assert result == {"barge_in": True, "tts": True, "transcriber": True}


async def test_health_check_returns_tts_false_when_ping_fails(make_hc_bridge):
    bridge, ws, orch = make_hc_bridge(input_mode="audio", output_mode=["audio", "text"])
    bridge.transcriber = MagicMock()
    bridge.transcriber.state = ConnectionState.CONNECTED

    bridge.tts.probe = AsyncMock(return_value=False)
    result = await bridge.health_check()

    assert result == {"barge_in": True, "tts": False, "transcriber": True}
    await sent(bridge)
    assert {"type": "status", "service": "tts", "state": "unavailable"} in ws.of("status")


async def test_health_check_returns_transcriber_false_when_degraded(make_hc_bridge):
    bridge, ws, orch = make_hc_bridge(input_mode="audio", output_mode=["text"])
    bridge.transcriber = MagicMock()
    bridge.transcriber.state = ConnectionState.DEGRADED

    result = await bridge.health_check()

    assert result == {"barge_in": False, "tts": False, "transcriber": False}
    await sent(bridge)
    assert {"type": "status", "service": "transcriber", "state": "unavailable"} in ws.of("status")


async def test_health_check_returns_all_false_for_text_only_handshake(make_hc_bridge):
    bridge, ws, orch = make_hc_bridge(input_mode="text", output_mode=["text"])
    bridge.transcriber = None

    result = await bridge.health_check()

    assert result == {"barge_in": False, "tts": False, "transcriber": False}


async def test_health_check_returns_none_when_orchestrator_unavailable(make_hc_bridge):
    bridge, ws, orch = make_hc_bridge()
    orch.ping = AsyncMock(return_value=False)

    result = await bridge.health_check()

    assert result is None
    msgs = await sent(bridge)
    assert msgs[0] == {"type": "status", "service": "orchestrator", "state": "unavailable"}
