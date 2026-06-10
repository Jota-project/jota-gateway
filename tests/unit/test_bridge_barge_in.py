"""Tests for barge-in: _cancel_active_turn, _on_transcription, close_all."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.models.schemas import Client, ClientConfig, Handshake

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


@pytest.fixture
def make_bridge(mock_tracker):
    def _make(input_mode="audio", output_mode=None):
        if output_mode is None:
            output_mode = ["audio", "text", "status"]
        ws = AsyncMock()
        bridge = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tracker=mock_tracker)
        bridge.handshake = Handshake(client_key="test-key", input_mode=input_mode, output_mode=output_mode)
        bridge.orchestrator = AsyncMock()
        bridge.orchestrator.close = AsyncMock()
        bridge.transcriber = MagicMock()
        bridge.transcriber._is_ready = True
        bridge.transcriber.close = AsyncMock()
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


# ── _on_transcription ────────────────────────────────────────────────────────

async def test_partial_below_threshold_forwarded_but_no_barge_in(make_bridge):
    """Partials shorter than BARGE_IN_MIN_CHARS are forwarded to client but don't trigger barge-in."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hi", False)  # 2 chars

    bridge.client_ws.send_json.assert_called_once_with({"type": "transcription_partial", "text": "hi"})
    assert bridge._active_turn is None


async def test_partial_above_threshold_with_no_active_turn_forwarded_only(make_bridge):
    """Partial above threshold but no active turn — forwarded to client, no barge-in needed."""
    bridge = make_bridge()

    await bridge._on_transcription("hello world", False)

    bridge.client_ws.send_json.assert_called_once_with({"type": "transcription_partial", "text": "hello world"})
    assert bridge._active_turn is None


async def test_partial_above_threshold_with_active_turn_triggers_barge_in(make_bridge):
    """Partial above threshold with active turn → forward partial + cancel + send interrupted."""
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._on_transcription("hello world", False)

    calls = bridge.client_ws.send_json.call_args_list
    assert calls[0][0][0] == {"type": "transcription_partial", "text": "hello world"}
    assert calls[1][0][0] == {"type": "interrupted"}
    assert bridge._active_turn is None


async def test_partial_does_not_call_orchestrator(make_bridge):
    """Partials — regardless of threshold — never call the orchestrator."""
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hello world", False)

    bridge._call_orchestrator.assert_not_called()


async def test_final_sends_transcription_to_client(make_bridge):
    """Final transcription → send {"type":"transcription"} to client."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola mundo", True)
    await asyncio.sleep(0)

    bridge.client_ws.send_json.assert_called_once_with(
        {"type": "transcription", "text": "hola mundo"}
    )


async def test_final_does_not_start_active_turn(make_bridge):
    """Final transcription no longer auto-dispatches to orchestrator.
    _active_turn stays None — client must send {"type":"send"} explicitly."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola", True)
    await asyncio.sleep(0)

    assert bridge._active_turn is None


async def test_final_cancels_previous_active_turn(make_bridge):
    """Final transcription cancels any in-progress turn but does NOT start a new one."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()
    old_turn = asyncio.create_task(asyncio.sleep(60))
    bridge._active_turn = old_turn
    await asyncio.sleep(0)

    await bridge._on_transcription("nueva frase", True)
    await asyncio.sleep(0)

    assert old_turn.cancelled()
    assert bridge._active_turn is None


async def test_final_with_disconnected_client_does_not_start_turn(make_bridge):
    """If send_json raises (disconnected client), no new turn is started."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

    await bridge._on_transcription("hola", True)

    assert bridge._active_turn is None


async def test_barge_in_interrupted_send_failure_is_silent(make_bridge):
    """If interrupted send fails, no exception propagates."""
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

    # Must not raise
    await bridge._on_transcription("hello world", False)


# ── close_all ────────────────────────────────────────────────────────────────

async def test_close_all_awaits_active_turn(make_bridge):
    """close_all() awaits _active_turn to completion (does not cancel it),
    so the orchestrator response is delivered before tearing down clients."""
    bridge = make_bridge()

    turn_ran = asyncio.Event()

    async def mock_turn():
        turn_ran.set()

    bridge._active_turn = asyncio.create_task(mock_turn())
    await asyncio.sleep(0)

    await bridge.close_all()

    assert turn_ran.is_set()


async def test_barge_in_uses_config_threshold_not_global(mock_tracker):
    """Bridge uses config.barge_in_min_chars, not settings.BARGE_IN_MIN_CHARS."""
    from src.models.schemas import ClientConfig
    ws = AsyncMock()
    config = ClientConfig(barge_in_min_chars=50)
    bridge = JotaBridge(client=_CLIENT, config=config, client_ws=ws, orchestrator=AsyncMock(), tracker=mock_tracker)
    bridge.handshake = Handshake(
        client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
    )
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    # 11 chars < 50 → no barge-in
    await bridge._on_transcription("hello world", False)

    calls = ws.send_json.call_args_list
    assert len(calls) == 1  # only the partial, no "interrupted"
    assert calls[0][0][0] == {"type": "transcription_partial", "text": "hello world"}
    bridge._active_turn.cancel()
    try:
        await bridge._active_turn
    except (asyncio.CancelledError, Exception):
        pass


async def test_barge_in_triggers_when_above_custom_threshold(mock_tracker):
    """Barge-in fires when partial >= config.barge_in_min_chars."""
    from src.models.schemas import ClientConfig
    ws = AsyncMock()
    config = ClientConfig(barge_in_min_chars=3)
    bridge = JotaBridge(client=_CLIENT, config=config, client_ws=ws, orchestrator=AsyncMock(), tracker=mock_tracker)
    bridge.handshake = Handshake(
        client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
    )
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._on_transcription("hola", False)  # 4 chars >= 3

    calls = ws.send_json.call_args_list
    assert any(c[0][0].get("type") == "interrupted" for c in calls)
