"""Tests for barge-in: _cancel_active_turn, _on_transcription, close_all."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from tests.fakes.speech import speech_providers

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


@pytest.fixture
def make_bridge(mock_tracker):
    def _make(input_mode="audio", output_mode=None):
        if output_mode is None:
            output_mode = ["audio", "text", "status"]
        ws = AsyncMock()
        bridge = JotaBridge(
            client=_CLIENT,
            config=_CONFIG,
            client_ws=ws,
            orchestrator=AsyncMock(),
            speech=speech_providers(),
            tracker=mock_tracker,
            handshake=Handshake(
                client_key="test-key", input_mode=input_mode, output_mode=output_mode
            ),
            client_registry=ClientRegistry(),
            default_agent="main",
        )
        bridge.orchestrator = AsyncMock()
        bridge.orchestrator.close = AsyncMock()
        bridge.transcriber = MagicMock()
        bridge.transcriber.close = AsyncMock()
        return bridge

    return _make


# ── _cancel_active_turn ──────────────────────────────────────────────────────


async def test_cancel_turn_caller_cancelled_while_waiting_is_not_swallowed(make_bridge):
    """#221: a caller cancelled while it waits for the old turn to finish gets its
    CancelledError, after the turn has finished and `_active_turn` was cleared."""
    bridge = make_bridge()

    async def slow_to_cancel():
        try:
            await asyncio.sleep(60)
        finally:
            await asyncio.shield(asyncio.sleep(0.05))

    turn = asyncio.create_task(slow_to_cancel())
    bridge._active_turn = turn
    await asyncio.sleep(0)

    caller = asyncio.create_task(bridge._cancel_turn())
    await asyncio.sleep(0.01)
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)

    assert caller.cancelled()
    assert turn.done()
    assert bridge._active_turn is None


async def test_inherited_cancellation_does_not_kill_the_barge_in_caller(make_bridge):
    """#221: only a cancellation received *while waiting* is re-raised. A task that
    carries an older, swallowed cancellation (cancelling() > 0) still completes the
    barge-in instead of dying in it."""
    bridge = make_bridge()
    turn = asyncio.create_task(asyncio.sleep(60))
    bridge._active_turn = turn
    await asyncio.sleep(0)

    async def caller_with_inherited_cancel() -> bool:
        me = asyncio.current_task()
        assert me is not None
        me.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass  # swallowed upstream: the counter stays at 1
        assert me.cancelling() == 1
        return await bridge._cancel_active_turn()

    assert await asyncio.create_task(caller_with_inherited_cancel()) is True
    assert turn.cancelled()
    assert bridge._active_turn is None


async def test_cancel_turn_logs_the_turn_failure_by_type(make_bridge, caplog):
    bridge = make_bridge()

    async def fails_on_cancel():
        try:
            await asyncio.sleep(60)
        finally:
            raise RuntimeError("secreto del turno")

    bridge._active_turn = asyncio.create_task(fails_on_cancel())
    await asyncio.sleep(0)
    with caplog.at_level(logging.DEBUG, logger="src.services.teardown"):
        assert await bridge._cancel_active_turn() is True
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged
    assert "secreto" not in logged


async def test_cancel_active_turn_returns_false_when_no_task(make_bridge):
    bridge = make_bridge()
    assert bridge._active_turn is None

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_returns_false_when_task_already_done(make_bridge):
    bridge = make_bridge()

    async def quick():
        pass

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

    bridge.client_ws.send_json.assert_called_once_with(
        {"type": "transcription_partial", "text": "hi"}
    )
    assert bridge._active_turn is None


async def test_partial_above_threshold_with_no_active_turn_forwarded_only(make_bridge):
    """Partial above threshold but no active turn — forwarded to client, no barge-in needed."""
    bridge = make_bridge()

    await bridge._on_transcription("hello world", False)

    bridge.client_ws.send_json.assert_called_once_with(
        {"type": "transcription_partial", "text": "hello world"}
    )
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


async def test_final_transcription_is_not_logged_at_info(make_bridge, caplog):
    """#106: the bridge's own final-transcription log moved from INFO to DEBUG.

    Scope is the `src.services.bridge` logger only; the `pipeline_tracker` side
    (#133) is covered in `test_transcript_privacy.py`.
    """
    bridge = make_bridge()
    text = "0123456789" * 4 + "TOP-SECRET-SUFFIX"

    with caplog.at_level(logging.INFO, logger="src.services.bridge"):
        await bridge._on_transcription(text, True)

    bridge_info_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "src.services.bridge" and r.levelno >= logging.INFO
    ]
    assert not any("Transcripción final" in m for m in bridge_info_logs)
    assert not any("TOP-SECRET-SUFFIX" in m for m in bridge_info_logs)


async def test_final_transcription_is_debug_only_and_truncated(make_bridge, caplog):
    bridge = make_bridge()
    text = "0123456789" * 4 + "TOP-SECRET-SUFFIX"

    with caplog.at_level(logging.DEBUG, logger="src.services.bridge"):
        await bridge._on_transcription(text, True)

    records = [record for record in caplog.records if "Transcripción final:" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert text[:40] in records[0].getMessage()
    assert text[40:] not in records[0].getMessage()


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
    bridge = JotaBridge(
        client=_CLIENT,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(),
        tracker=mock_tracker,
        handshake=Handshake(
            client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
        ),
        client_registry=ClientRegistry(),
        default_agent="main",
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
    bridge = JotaBridge(
        client=_CLIENT,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(),
        tracker=mock_tracker,
        handshake=Handshake(
            client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
        ),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._on_transcription("hola", False)  # 4 chars >= 3

    calls = ws.send_json.call_args_list
    assert any(c[0][0].get("type") == "interrupted" for c in calls)


async def test_barge_in_disabled_forwards_partial_but_does_not_cancel(mock_tracker):
    """#108: barge_in_enabled=False → partial still forwarded, but no cancellation
    and no 'interrupted' message, even when text is above barge_in_min_chars."""
    from src.models.schemas import ClientConfig

    ws = AsyncMock()
    config = ClientConfig(barge_in_enabled=False, barge_in_min_chars=3)
    bridge = JotaBridge(
        client=_CLIENT,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(),
        tracker=mock_tracker,
        handshake=Handshake(
            client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
        ),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    # 10 chars >= barge_in_min_chars(3), but barge-in is disabled
    await bridge._on_transcription("hola mundo", False)

    calls = ws.send_json.call_args_list
    assert len(calls) == 1  # only the partial, no "interrupted"
    assert calls[0][0][0] == {"type": "transcription_partial", "text": "hola mundo"}
    assert bridge._active_turn is not None
    assert not bridge._active_turn.done()

    bridge._active_turn.cancel()
    try:
        await bridge._active_turn
    except (asyncio.CancelledError, Exception):
        pass


# ── _on_transcriber_warning ──────────────────────────────────────────────────


async def test_transcriber_warning_forwarded_to_client(make_bridge):
    bridge = make_bridge()
    await bridge._on_transcriber_warning("buffer_full", "Buffer full")
    bridge.client_ws.send_json.assert_called_once_with(
        {
            "type": "status",
            "service": "transcriber",
            "state": "degraded",
            "code": "buffer_full",
            "message": "Buffer full",
        }
    )


async def test_transcriber_warning_uses_code_when_no_message(make_bridge):
    bridge = make_bridge()
    await bridge._on_transcriber_warning("timeout", None)
    payload = bridge.client_ws.send_json.call_args[0][0]
    assert payload["type"] == "status"
    assert payload["state"] == "degraded"
    assert payload["message"] == "timeout"


async def test_transcriber_warning_send_failure_is_silent(make_bridge):
    bridge = make_bridge()
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    await bridge._on_transcriber_warning("buffer_full", "Buffer full")
    # Must not raise
