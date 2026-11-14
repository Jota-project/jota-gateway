"""Tests for barge-in: TurnController.cancel, _on_transcription, close_all."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from fogar_gateway.models.schemas import ClientConfig
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.fakes.bridge import GoneSender, blocking, sent, start_turn

_AUDIO = dict(input_mode="audio", output_mode=("audio", "text", "status"))


@pytest.fixture
def make_bridge(make_bridge):
    """A voice client whose TTS is down (no stream): these tests are about the turns."""

    def _make(**kw):
        tts = AsyncMock()
        tts.connect = AsyncMock(return_value=None)
        tts.resolve_voice = lambda v: v
        return make_bridge(tts=tts, **kw)

    return _make


def _transcriber():
    t = MagicMock()
    t.close = AsyncMock()
    return t


@pytest.fixture
async def bridge(make_bridge):
    b, _ = make_bridge(**_AUDIO, transcriber=_transcriber())
    return b


def _stream_cancelled_slowly(started: asyncio.Event, *, finally_raises: Exception | None = None):
    """An orchestrator stream that emits one token, then waits; on cancellation it takes
    a moment (or raises `finally_raises`)."""

    async def _s(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            await asyncio.shield(asyncio.sleep(0.05))
            if finally_raises is not None:
                raise finally_raises

    return _s


async def _running_turn(bridge, stream=None) -> asyncio.Task:
    started = asyncio.Event()
    bridge.orchestrator.stream_response = stream(started) if stream else blocking(started)
    task = await start_turn(bridge)
    await asyncio.wait_for(started.wait(), 2)
    return task


# ── TurnController.cancel ────────────────────────────────────────────────────


async def test_cancel_turn_caller_cancelled_while_waiting_is_not_swallowed(bridge):
    """#221: a caller cancelled while it waits for the old turn to finish gets its
    CancelledError, after the turn has finished (and sent its turn_end)."""
    turn = await _running_turn(bridge, _stream_cancelled_slowly)

    caller = asyncio.create_task(bridge.turns.cancel("cancelled"))
    await asyncio.sleep(0.01)
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)

    assert caller.cancelled()
    assert turn.done()
    assert bridge.turns.user_task is None
    assert [m["turn_id"] for m in (await sent(bridge)) if m["type"] == "turn_end"] == ["t-1"]


async def test_inherited_cancellation_does_not_kill_the_barge_in_caller(bridge):
    """#221: only a cancellation received *while waiting* is re-raised. A task that
    carries an older, swallowed cancellation (cancelling() > 0) still completes the
    barge-in instead of dying in it."""
    turn = await _running_turn(bridge)

    async def caller_with_inherited_cancel():
        me = asyncio.current_task()
        assert me is not None
        me.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass  # swallowed upstream: the counter stays at 1
        assert me.cancelling() == 1
        return await bridge.turns.cancel("interrupted")

    assert await asyncio.create_task(caller_with_inherited_cancel()) == (True, "t-1")
    assert turn.done()
    assert bridge.turns.user_task is None


async def test_cancel_turn_logs_the_turn_failure_by_type(bridge, caplog):
    started = asyncio.Event()
    bridge.orchestrator.stream_response = _stream_cancelled_slowly(
        started, finally_raises=RuntimeError("secreto del turno")
    )
    await start_turn(bridge)
    await asyncio.wait_for(started.wait(), 2)
    with caplog.at_level(logging.DEBUG):
        assert await bridge.turns.cancel("cancelled") == (True, "t-1")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged
    assert "secreto" not in logged
    # the broken turn still got its one turn_end
    assert [m["turn_id"] for m in (await sent(bridge)) if m["type"] == "turn_end"] == ["t-1"]


async def test_cancel_with_no_turn_returns_false(bridge):
    assert await bridge.turns.cancel("cancelled") == (False, None)


async def test_cancel_after_the_turn_ended_returns_false(bridge):
    async def done(*a, **k):
        yield OrchestratorEvent(type="token", content="x")

    bridge.orchestrator.stream_response = done
    task = await start_turn(bridge)
    await asyncio.wait({task})

    assert await bridge.turns.cancel("cancelled") == (False, None)


async def test_cancel_cancels_the_running_turn(bridge):
    turn = await _running_turn(bridge)

    assert await bridge.turns.cancel("cancelled") == (True, "t-1")

    assert turn.cancelled()
    assert bridge.turns.user_task is None
    assert bridge.turns.snapshot(0.0)["turn"]["kind"] == "idle"


# ── _on_transcription ────────────────────────────────────────────────────────


async def test_partial_below_threshold_forwarded_but_no_barge_in(bridge):
    """Partials shorter than BARGE_IN_MIN_CHARS are forwarded to client but don't trigger barge-in."""
    turn = await _running_turn(bridge)
    await sent(bridge)
    bridge.client_ws.sent.clear()

    await bridge._on_transcription("hi", False)  # 2 chars

    assert await sent(bridge) == [{"type": "transcription_partial", "text": "hi"}]
    assert not turn.done()
    turn.cancel()


async def test_partial_above_threshold_with_no_active_turn_forwarded_only(bridge):
    """Partial above threshold but no active turn — forwarded to client, no barge-in needed."""

    await bridge._on_transcription("hello world", False)

    assert await sent(bridge) == [{"type": "transcription_partial", "text": "hello world"}]
    assert bridge.turns.user_task is None


async def test_partial_does_not_call_orchestrator(bridge):
    """Partials — regardless of threshold — never call the orchestrator."""
    calls = []

    async def s(*a, **k):
        calls.append(1)
        yield OrchestratorEvent(type="token", content="x")

    bridge.orchestrator.stream_response = s

    await bridge._on_transcription("hello world", False)
    await asyncio.sleep(0.01)

    assert calls == []
    assert bridge.turns.user_task is None


async def test_final_sends_transcription_to_client(bridge):
    """Final transcription → send {"type":"transcription"} to client."""
    await bridge._on_transcription("hola mundo", True)
    await asyncio.sleep(0)

    assert await sent(bridge) == [{"type": "transcription", "text": "hola mundo"}]


async def test_final_transcription_is_not_logged_at_info(bridge, caplog):
    """#106: the bridge's own final-transcription log moved from INFO to DEBUG.

    Scope is the `fogar_gateway.services.bridge` logger only; the `pipeline_tracker` side
    (#133) is covered in `test_transcript_privacy.py`.
    """
    text = "0123456789" * 4 + "TOP-SECRET-SUFFIX"

    with caplog.at_level(logging.INFO, logger="fogar_gateway.services.bridge"):
        await bridge._on_transcription(text, True)

    bridge_info_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "fogar_gateway.services.bridge" and r.levelno >= logging.INFO
    ]
    assert not any("Transcripción final" in m for m in bridge_info_logs)
    assert not any("TOP-SECRET-SUFFIX" in m for m in bridge_info_logs)


async def test_final_transcription_is_debug_only_and_truncated(bridge, caplog):
    text = "0123456789" * 4 + "TOP-SECRET-SUFFIX"

    with caplog.at_level(logging.DEBUG, logger="fogar_gateway.services.bridge"):
        await bridge._on_transcription(text, True)

    records = [record for record in caplog.records if "Transcripción final:" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert text[:40] in records[0].getMessage()
    assert text[40:] not in records[0].getMessage()


async def test_final_does_not_start_active_turn(bridge):
    """Final transcription no longer auto-dispatches to orchestrator: no turn starts —
    the client must send {"type":"send"} explicitly."""
    await bridge._on_transcription("hola", True)
    await asyncio.sleep(0)

    assert bridge.turns.user_task is None
    assert [m["type"] for m in await sent(bridge)] == ["transcription"]


async def test_final_cancels_previous_active_turn(bridge):
    """Final transcription cancels any in-progress turn but does NOT start a new one."""
    old_turn = await _running_turn(bridge)

    await bridge._on_transcription("nueva frase", True)
    await asyncio.sleep(0)

    assert old_turn.cancelled()
    assert bridge.turns.user_task is None
    assert [m["type"] for m in await sent(bridge)][-2:] == ["turn_end", "transcription"]


async def test_final_with_disconnected_client_does_not_start_turn(make_bridge):
    """If the client is gone, no new turn is started."""
    bridge, _ = make_bridge(**_AUDIO, transcriber=_transcriber(), sender=GoneSender())

    await bridge._on_transcription("hola", True)

    assert bridge.turns.user_task is None


async def test_barge_in_interrupted_send_failure_is_silent(make_bridge):
    """If interrupted send fails, no exception propagates."""
    bridge, _ = make_bridge(**_AUDIO, transcriber=_transcriber(), sender=GoneSender())
    started = asyncio.Event()

    async def s(*a, **k):
        started.set()
        await asyncio.sleep(60)
        yield OrchestratorEvent(type="token", content="x")  # pragma: no cover

    bridge.orchestrator.stream_response = s
    task = await start_turn(bridge)  # its turn_start fails: the client is gone
    await asyncio.wait({task}, timeout=1)

    # Must not raise
    await bridge._on_transcription("hello world", False)


# ── close_all ────────────────────────────────────────────────────────────────


async def test_close_all_ends_the_active_turn(bridge):
    """close_all() cancels the turn in flight (7d) and the client still gets its turn_end."""
    turn = await _running_turn(bridge)

    await bridge.close_all()

    assert turn.done()
    assert [m["turn_id"] for m in bridge.client_ws.of("turn_end")] == ["t-1"]
    assert bridge.turns.last_ended.end_reason == "closed"


@pytest.mark.parametrize(
    ("config", "text", "barges"),
    [
        pytest.param(ClientConfig(), "hello world", True, id="default-threshold"),
        pytest.param(ClientConfig(barge_in_min_chars=5), "a" * 5, True, id="exactly-min-chars"),
        pytest.param(ClientConfig(barge_in_min_chars=5), "a" * 4, False, id="one-below-min"),
        pytest.param(ClientConfig(barge_in_min_chars=3), "hola", True, id="custom-threshold"),
        # config.barge_in_min_chars, not settings.BARGE_IN_MIN_CHARS
        pytest.param(ClientConfig(barge_in_min_chars=50), "hello world", False, id="config-not-global"),
        # #108: disabled -> partial still forwarded, no cancellation, no turn_end
        pytest.param(
            ClientConfig(barge_in_enabled=False, barge_in_min_chars=3), "hola mundo", False, id="disabled"
        ),
    ],
)
async def test_partial_with_active_turn_barges_in_from_the_client_threshold(
    make_bridge, config, text, barges
):
    bridge, ws = make_bridge(**_AUDIO, transcriber=_transcriber(), config=config)
    turn = await _running_turn(bridge)
    await sent(bridge)
    ws.sent.clear()

    await bridge._on_transcription(text, False)

    msgs = [m for m in await sent(bridge) if isinstance(m, dict)]
    assert msgs[0] == {"type": "transcription_partial", "text": text}
    # M19: the barge-in is the turn_end's reason; there is no separate `interrupted` message.
    expected = [{"type": "turn_end", "turn_id": "t-1", "reason": "interrupted"}]
    assert msgs[1:] == (expected if barges else [])
    assert turn.cancelled() is barges
    assert (bridge.turns.user_task is None) is barges
    turn.cancel()


async def test_barge_in_before_turn_start_sends_nothing_but_the_partial(make_bridge):
    """A turn cancelled before it sent its turn_start: no turn_end (M19: nor `interrupted`)."""
    bridge, ws = make_bridge(**_AUDIO, transcriber=_transcriber())
    bridge.orchestrator.stream_response = blocking()
    await bridge.turns.start_user_turn("x")  # the task has not run yet

    await bridge._on_transcription("hello world", False)

    assert [m for m in await sent(bridge) if isinstance(m, dict)] == [
        {"type": "transcription_partial", "text": "hello world"},
    ]


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
