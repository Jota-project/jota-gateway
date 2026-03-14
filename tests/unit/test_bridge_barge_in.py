"""Tests for barge-in: _cancel_active_turn, _on_transcription, close_all."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def make_bridge():
    def _make(input_mode="audio", output_mode=None):
        if output_mode is None:
            output_mode = ["audio", "text", "status"]
        ws = AsyncMock()
        bridge = JotaBridge(client_id="test", client_ws=ws)
        bridge.handshake = Handshake(input_mode=input_mode, output_mode=output_mode)
        bridge.orchestrator = AsyncMock()
        bridge.transcriber = MagicMock()
        bridge.transcriber._is_ready = True
        return bridge
    return _make


# ── _cancel_active_turn ──────────────────────────────────────────────────────

async def test_cancel_active_turn_returns_false_when_no_task(make_bridge):
    bridge = make_bridge()
    assert bridge._active_turn is None

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_returns_false_when_task_already_done(make_bridge):
    bridge = make_bridge()

    async def quick(): pass
    bridge._active_turn = asyncio.create_task(quick())
    await asyncio.sleep(0)  # let task complete naturally
    assert bridge._active_turn.done()

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_cancels_running_task(make_bridge):
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)  # let task start

    result = await bridge._cancel_active_turn()

    assert result is True
    assert bridge._active_turn is None


async def test_cancel_active_turn_clears_active_turn(make_bridge):
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._cancel_active_turn()

    assert bridge._active_turn is None
