"""Issue #117: an idle-but-connected bridge that never attempts a turn
itself must still learn about a TTS outage/recovery driven by a different
session's turns — before this fix, only the bridge making the failing/
succeeding turn found out, via the now-removed per-bridge
_maybe_notify_tts_state()/_tts_degraded_notified polling.

Newly-connecting sessions starting mid-outage are a separate concern,
already covered by health_check()'s pre-existing live TTS ping
(bridge.py's health_check()) — not duplicated here."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from jota_gateway.services.openclaw.registry import ClientRegistry
from jota_gateway.services.reconnection import ConnectionState, to_wire_state
from jota_gateway.services.tts_reconnecting import ReconnectingTTSClient
from tests.fakes.bridge import sent


@pytest.mark.asyncio
async def test_tts_outage_and_recovery_broadcasts_to_all_registered_bridges(make_bridge):
    """Mirrors main.py's lifespan wiring: ReconnectingTTSClient.on_state_change
    is wired to ClientRegistry.broadcast_status, so an idle bridge that never
    attempts a turn itself still learns about an outage and recovery driven
    by a different session's turns."""
    registry = ClientRegistry()
    tts = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=0.05)
    notification_tasks: set[asyncio.Task] = set()

    def _on_tts_state_change(state: ConnectionState) -> None:
        task = asyncio.create_task(registry.broadcast_status("tts", to_wire_state(state)))
        notification_tasks.add(task)
        task.add_done_callback(notification_tasks.discard)

    tts.on_state_change = _on_tts_state_change

    idle_bridge, ws = make_bridge(output_mode=("audio", "text"), tts=AsyncMock())
    registry.register("idle-client", idle_bridge)

    with patch("jota_gateway.services.tts_reconnecting.TTSClient") as MockTTS:
        failing_client = AsyncMock()
        failing_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = failing_client
        await tts.connect(voice=None, speed=None, client_id="other-session")
        await asyncio.sleep(0.01)  # let the fire-and-forget broadcast task run

        time.sleep(0.1)  # let the backoff window elapse
        succeeding_client = AsyncMock()
        MockTTS.return_value = succeeding_client
        await tts.connect(voice=None, speed=None, client_id="other-session")
        await asyncio.sleep(0.01)

    await sent(idle_bridge)
    assert [m for m in ws.of("status") if m["service"] == "tts"] == [
        {"type": "status", "service": "tts", "state": "reconnecting"},
        {"type": "status", "service": "tts", "state": "restored"},
    ]
