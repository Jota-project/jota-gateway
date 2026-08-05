"""Issue #117: newly-connecting sessions must learn the TTS wrapper's
*current* state at session start, not just react to failures on their own
first turn — an idle-but-connected client already gets this via the
on_state_change → ClientRegistry.broadcast_status wiring (main.py), but a
brand new session starting mid-outage would otherwise stay silent about it
until its own first turn attempt."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.reconnection import ConnectionState, ServiceStatus, to_wire_state
from src.services.tts_reconnecting import ReconnectingTTSClient

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _status(state: ConnectionState) -> ServiceStatus:
    return ServiceStatus(
        name="tts", state=state, connected_at=None, reconnect_attempts=0, last_error=None
    )


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
async def test_connect_internal_services_notifies_when_tts_already_degraded():
    tts = AsyncMock()
    tts.status = lambda: _status(ConnectionState.RECONNECTING)
    bridge = _make_bridge(output_mode=["audio", "text"], tts=tts)

    await bridge.connect_internal_services()

    assert _tts_status_messages(bridge) == [
        {"type": "status", "service": "tts", "state": "reconnecting"}
    ]


@pytest.mark.asyncio
async def test_connect_internal_services_does_not_notify_when_tts_connected():
    tts = AsyncMock()
    tts.status = lambda: _status(ConnectionState.CONNECTED)
    bridge = _make_bridge(output_mode=["audio", "text"], tts=tts)

    await bridge.connect_internal_services()

    assert _tts_status_messages(bridge) == []


@pytest.mark.asyncio
async def test_connect_internal_services_skips_tts_check_when_audio_not_requested():
    # tts.status left unstubbed on purpose: an AsyncMock's auto-mocked child
    # attributes return a coroutine when called, so this would blow up with
    # an AttributeError on .state if the code under test ever called it for
    # a text-only session — asserting that proves the audio gate is in place.
    tts = AsyncMock()
    bridge = _make_bridge(output_mode=["text"], tts=tts)

    await bridge.connect_internal_services()  # must not raise

    assert _tts_status_messages(bridge) == []


@pytest.mark.asyncio
async def test_tts_outage_and_recovery_broadcasts_to_all_registered_bridges():
    """Mirrors main.py's lifespan wiring: ReconnectingTTSClient.on_state_change
    is wired to ClientRegistry.broadcast_status, so an idle bridge that never
    attempts a turn itself still learns about an outage and recovery driven
    by a different session's turns — closing the gap the old per-bridge
    _tts_degraded_notified polling had (idle clients only found out on their
    own next turn)."""
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
