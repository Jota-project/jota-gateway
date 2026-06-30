"""Tests for JotaBridge._transcription_watchdog — two paths:
silence timeout fires, and early exit when transcriber disconnects.
"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.pipeline_tracker import PipelineTracker, _NullWS
from src.core.config import settings

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _make_bridge():
    ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:wd", client_id="hab_sito",
        input_mode="audio", output_mode=["text"],
        client_ws=_NullWS(), registry=registry,
    )
    handshake = Handshake(client_key="test-key", input_mode="audio", output_mode=["text"])
    orch = AsyncMock()
    transcriber = MagicMock()
    transcriber._is_ready = True
    transcriber._last_transcription_at = None
    bridge = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws,
                        orchestrator=orch, tracker=tracker, handshake=handshake,
                        client_registry=ClientRegistry(), default_agent="main")
    bridge.transcriber = transcriber
    return bridge, ws, transcriber


@pytest.mark.asyncio
async def test_watchdog_notifies_client_after_silence_timeout(monkeypatch):
    """After TRANSCRIBER_SILENCE_TIMEOUT_S with no transcription, client gets degraded notice."""
    monkeypatch.setattr(settings, "TRANSCRIBER_SILENCE_TIMEOUT_S", 1)
    bridge, ws, transcriber = _make_bridge()
    # Simulate audio started 2s ago — already past the 1s timeout
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = None

    # Patch asyncio.sleep so the 2s poll interval doesn't slow the test
    with patch("src.services.bridge.asyncio.sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "service_status"
    assert payload["service"] == "transcriber"
    assert payload["status"] == "degraded"


@pytest.mark.asyncio
async def test_watchdog_exits_if_transcriber_disconnects():
    """Watchdog exits cleanly when transcriber goes offline."""
    bridge, ws, transcriber = _make_bridge()
    bridge._first_audio_at = time.monotonic()
    transcriber._is_ready = False

    # Patch asyncio.sleep so the 2s poll interval doesn't slow the test
    with patch("src.services.bridge.asyncio.sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)
