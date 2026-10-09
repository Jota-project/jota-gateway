"""Transcriber capacity-status handling in JotaBridge (issue #143)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.reconnection import ConnectionState


@pytest.fixture
def bridge(mock_tracker):
    b = JotaBridge(
        client=Client(id="c1", client_key="k", is_active=True),
        config=ClientConfig(silence_timeout_s=1, max_silence_turns=2),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="k", input_mode="audio", output_mode=["text"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    b.transcriber = MagicMock()
    b.transcriber.state = ConnectionState.CONNECTED
    b.transcriber._last_transcription_at = None
    return b


def _sent(bridge):
    return [c.args[0] for c in bridge.client_ws.send_json.await_args_list]


async def test_busy_notifies_degraded_with_code(bridge):
    await bridge._on_transcriber_status("busy", "gpu_saturated")

    assert _sent(bridge) == [
        {
            "type": "status",
            "service": "transcriber",
            "state": "degraded",
            "code": "gpu_saturated",
            "message": "gpu_saturated",
        }
    ]


async def test_ok_after_busy_notifies_restored(bridge):
    await bridge._on_transcriber_status("busy", "gpu_saturated")
    await bridge._on_transcriber_status("ok", None)

    assert _sent(bridge)[-1] == {"type": "status", "service": "transcriber", "state": "restored"}


async def test_duplicate_busy_and_initial_ok_are_not_notified(bridge):
    await bridge._on_transcriber_status("ok", None)
    assert _sent(bridge) == []

    await bridge._on_transcriber_status("busy", "gpu_saturated")
    await bridge._on_transcriber_status("busy", "gpu_saturated")
    assert len(_sent(bridge)) == 1

    await bridge._on_transcriber_status("ok", None)
    await bridge._on_transcriber_status("ok", None)
    assert len(_sent(bridge)) == 2


async def test_unknown_state_is_ignored(bridge):
    await bridge._on_transcriber_status("on_fire", None)

    assert _sent(bridge) == []
    assert bridge._transcriber_busy is False


async def test_warning_goes_through_notify_service_status(bridge):
    await bridge._on_transcriber_warning("buffer_full", None)

    assert _sent(bridge) == [
        {
            "type": "status",
            "service": "transcriber",
            "state": "degraded",
            "code": "buffer_full",
            "message": "buffer_full",
        }
    ]


async def test_incomplete_final_is_recorded_in_tracker_without_text(bridge):
    await bridge._on_transcriber_incomplete("gpu_saturated_timeout")

    event = next(e for e in bridge.tracker.events if e.stage == "transcription_incomplete")
    assert event.meta == {"reason": "gpu_saturated_timeout"}


async def test_watchdog_does_not_count_silence_while_transcriber_busy(bridge):
    bridge._first_audio_at = time.monotonic() - 10
    bridge._transcriber_busy = True
    ticks = {"n": 0}

    async def _sleep(_):
        ticks["n"] += 1
        if ticks["n"] == 5:
            bridge.transcriber.state = ConnectionState.DEGRADED  # stop the loop

    bridge.close_all = AsyncMock()
    with patch("src.services.bridge.asyncio.sleep", new=_sleep):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    bridge.close_all.assert_not_awaited()
    assert not any(m.get("state") == "degraded" for m in _sent(bridge))


async def test_watchdog_grants_fresh_baseline_after_busy_clears(bridge):
    bridge._first_audio_at = time.monotonic() - 10
    bridge.transcriber._last_transcription_at = time.monotonic() - 10
    bridge._transcriber_busy = True
    ticks = {"n": 0}

    async def _sleep(_):
        ticks["n"] += 1
        if ticks["n"] == 3:
            bridge._transcriber_busy = False  # saturation over, no transcript yet
        elif ticks["n"] == 4:
            bridge.transcriber.state = ConnectionState.DEGRADED  # stop the loop

    bridge.close_all = AsyncMock()
    with patch("src.services.bridge.asyncio.sleep", new=_sleep):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    bridge.close_all.assert_not_awaited()
