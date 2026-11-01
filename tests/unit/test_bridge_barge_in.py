"""Tests for barge-in: _cancel_active_turn, _on_transcription, close_all."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.schemas import ClientConfig
from tests.fakes.bridge import GoneSender, sent

_AUDIO = dict(input_mode="audio", output_mode=("audio", "text", "status"))


def _transcriber():
    t = MagicMock()
    t.close = AsyncMock()
    return t


@pytest.fixture
async def bridge(make_bridge):
    b, _ = make_bridge(**_AUDIO, transcriber=_transcriber())
    return b


# ── _cancel_active_turn ──────────────────────────────────────────────────────


async def test_cancel_turn_caller_cancelled_while_waiting_is_not_swallowed(bridge):
    """#221: a caller cancelled while it waits for the old turn to finish gets its
    CancelledError, after the turn has finished and `_active_turn` was cleared."""

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


async def test_inherited_cancellation_does_not_kill_the_barge_in_caller(bridge):
    """#221: only a cancellation received *while waiting* is re-raised. A task that
    carries an older, swallowed cancellation (cancelling() > 0) still completes the
    barge-in instead of dying in it."""
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


async def test_cancel_turn_logs_the_turn_failure_by_type(bridge, caplog):

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


async def test_cancel_active_turn_returns_false_when_no_task(bridge):
    assert bridge._active_turn is None

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_returns_false_when_task_already_done(bridge):

    async def quick():
        pass

    bridge._active_turn = asyncio.create_task(quick())
    await asyncio.sleep(0)  # let task complete naturally
    assert bridge._active_turn.done()

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_cancels_running_task(bridge):
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)  # let task start

    result = await bridge._cancel_active_turn()

    assert result is True
    assert bridge._active_turn is None


async def test_cancel_active_turn_clears_active_turn(bridge):
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._cancel_active_turn()

    assert bridge._active_turn is None


# ── _on_transcription ────────────────────────────────────────────────────────


async def test_partial_below_threshold_forwarded_but_no_barge_in(bridge):
    """Partials shorter than BARGE_IN_MIN_CHARS are forwarded to client but don't trigger barge-in."""
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hi", False)  # 2 chars

    assert await sent(bridge) == [{"type": "transcription_partial", "text": "hi"}]
    assert bridge._active_turn is None


async def test_partial_above_threshold_with_no_active_turn_forwarded_only(bridge):
    """Partial above threshold but no active turn — forwarded to client, no barge-in needed."""

    await bridge._on_transcription("hello world", False)

    assert await sent(bridge) == [{"type": "transcription_partial", "text": "hello world"}]
    assert bridge._active_turn is None


async def test_partial_does_not_call_orchestrator(bridge):
    """Partials — regardless of threshold — never call the orchestrator."""
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hello world", False)

    bridge._call_orchestrator.assert_not_called()


async def test_final_sends_transcription_to_client(bridge):
    """Final transcription → send {"type":"transcription"} to client."""
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola mundo", True)
    await asyncio.sleep(0)

    assert await sent(bridge) == [{"type": "transcription", "text": "hola mundo"}]


async def test_final_transcription_is_not_logged_at_info(bridge, caplog):
    """#106: the bridge's own final-transcription log moved from INFO to DEBUG.

    Scope is the `src.services.bridge` logger only; the `pipeline_tracker` side
    (#133) is covered in `test_transcript_privacy.py`.
    """
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


async def test_final_transcription_is_debug_only_and_truncated(bridge, caplog):
    text = "0123456789" * 4 + "TOP-SECRET-SUFFIX"

    with caplog.at_level(logging.DEBUG, logger="src.services.bridge"):
        await bridge._on_transcription(text, True)

    records = [record for record in caplog.records if "Transcripción final:" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert text[:40] in records[0].getMessage()
    assert text[40:] not in records[0].getMessage()


async def test_final_does_not_start_active_turn(bridge):
    """Final transcription no longer auto-dispatches to orchestrator.
    _active_turn stays None — client must send {"type":"send"} explicitly."""
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola", True)
    await asyncio.sleep(0)

    assert bridge._active_turn is None


async def test_final_cancels_previous_active_turn(bridge):
    """Final transcription cancels any in-progress turn but does NOT start a new one."""
    bridge._call_orchestrator = AsyncMock()
    old_turn = asyncio.create_task(asyncio.sleep(60))
    bridge._active_turn = old_turn
    await asyncio.sleep(0)

    await bridge._on_transcription("nueva frase", True)
    await asyncio.sleep(0)

    assert old_turn.cancelled()
    assert bridge._active_turn is None


async def test_final_with_disconnected_client_does_not_start_turn(make_bridge):
    """If the client is gone, no new turn is started."""
    bridge, _ = make_bridge(**_AUDIO, transcriber=_transcriber(), sender=GoneSender())
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola", True)

    assert bridge._active_turn is None


async def test_barge_in_interrupted_send_failure_is_silent(make_bridge):
    """If interrupted send fails, no exception propagates."""
    bridge, _ = make_bridge(**_AUDIO, transcriber=_transcriber(), sender=GoneSender())
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    # Must not raise
    await bridge._on_transcription("hello world", False)


# ── close_all ────────────────────────────────────────────────────────────────


async def test_close_all_awaits_active_turn(bridge):
    """close_all() awaits _active_turn to completion (does not cancel it),
    so the orchestrator response is delivered before tearing down clients."""

    turn_ran = asyncio.Event()

    async def mock_turn():
        turn_ran.set()

    bridge._active_turn = asyncio.create_task(mock_turn())
    await asyncio.sleep(0)

    await bridge.close_all()

    assert turn_ran.is_set()


@pytest.mark.parametrize(
    ("config", "text", "barges"),
    [
        pytest.param(ClientConfig(), "hello world", True, id="default-threshold"),
        pytest.param(ClientConfig(barge_in_min_chars=5), "a" * 5, True, id="exactly-min-chars"),
        pytest.param(ClientConfig(barge_in_min_chars=5), "a" * 4, False, id="one-below-min"),
        pytest.param(ClientConfig(barge_in_min_chars=3), "hola", True, id="custom-threshold"),
        # config.barge_in_min_chars, not settings.BARGE_IN_MIN_CHARS
        pytest.param(ClientConfig(barge_in_min_chars=50), "hello world", False, id="config-not-global"),
        # #108: disabled -> partial still forwarded, no cancellation, no `interrupted`
        pytest.param(
            ClientConfig(barge_in_enabled=False, barge_in_min_chars=3), "hola mundo", False, id="disabled"
        ),
    ],
)
async def test_partial_with_active_turn_barges_in_from_the_client_threshold(
    make_bridge, config, text, barges
):
    bridge, ws = make_bridge(**_AUDIO, transcriber=_transcriber(), config=config)
    turn = asyncio.create_task(asyncio.sleep(60))
    bridge._active_turn = turn
    await asyncio.sleep(0)

    await bridge._on_transcription(text, False)

    msgs = await sent(bridge)
    assert msgs[0] == {"type": "transcription_partial", "text": text}
    assert msgs[1:] == ([{"type": "interrupted"}] if barges else [])
    assert turn.cancelled() is barges
    turn.cancel()


# ── _on_transcriber_warning ──────────────────────────────────────────────────


async def test_transcriber_warning_forwarded_to_client(bridge):
    await bridge._on_transcriber_warning("buffer_full", "Buffer full")
    assert await sent(bridge) == [
        {
            "type": "status",
            "service": "transcriber",
            "state": "degraded",
            "code": "buffer_full",
            "message": "Buffer full",
        }
    ]


async def test_transcriber_warning_uses_code_when_no_message(bridge):
    await bridge._on_transcriber_warning("timeout", None)
    (payload,) = await sent(bridge)
    assert payload["type"] == "status"
    assert payload["state"] == "degraded"
    assert payload["message"] == "timeout"


async def test_transcriber_warning_send_failure_is_silent(make_bridge):
    bridge, _ = make_bridge(**_AUDIO, transcriber=_transcriber(), sender=GoneSender())
    await bridge._on_transcriber_warning("buffer_full", "Buffer full")
    # Must not raise
