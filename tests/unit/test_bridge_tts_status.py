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

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.reconnection import ConnectionState, to_wire_state
from src.services.tts_reconnecting import ReconnectingTTSClient

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _make_bridge(output_mode: list[str], tts) -> JotaBridge:
    return JotaBridge(
        client=_CLIENT,
        config=_CONFIG,
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=tts,
        tracker=AsyncMock(),
        handshake=Handshake(client_key="test-key", input_mode="text", output_mode=output_mode),
        client_registry=ClientRegistry(),
        default_agent="main",
    )


def _tts_status_messages(bridge: JotaBridge) -> list[dict]:
    return [
        call.args[0]
        for call in bridge.client_ws.send_json.await_args_list
        if call.args[0].get("type") == "status" and call.args[0].get("service") == "tts"
    ]


@pytest.mark.asyncio
async def test_tts_outage_and_recovery_broadcasts_to_all_registered_bridges():
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

    idle_bridge = _make_bridge(output_mode=["audio", "text"], tts=AsyncMock())
    registry.register("idle-client", idle_bridge)

    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
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

    assert _tts_status_messages(idle_bridge) == [
        {"type": "status", "service": "tts", "state": "reconnecting"},
        {"type": "status", "service": "tts", "state": "restored"},
    ]
