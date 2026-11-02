"""Tests for JotaBridge.close_all() idempotency (issue #101).

routes.py wraps connect_internal_services/health_check/ready-send/run() in a
single try/finally that unconditionally calls close_all() on exit. In the
normal-completion path, bridge.run() has *already* called close_all() from
its own internal finally before that outer finally runs — so close_all()
must tolerate being called twice without duplicating side effects, and the
first call's status must win.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.openclaw.registry import ClientRegistry
from src.services.protocol import OrchestratorEvent
from tests.fakes.bridge import sent


@pytest.fixture
async def bridge(make_bridge, mock_tracker):
    b, _ = make_bridge(client_id="test-uuid", tracker=mock_tracker, register=True)
    return b


async def test_close_all_twice_has_the_effects_of_one(bridge):
    """Also the M6 exit-gate mutant on the `_closed` guard."""
    bridge.transcriber = MagicMock()
    bridge.transcriber.close = AsyncMock()

    await bridge.close_all()
    await bridge.close_all()  # must not raise

    assert bridge._closed is True
    assert bridge.transcriber.close.await_count == 1
    assert [e.stage for e in bridge.tracker.events].count("session_end") == 1
    bridge.tracker._registry.close.assert_called_once()


async def test_close_all_unregisters_from_client_registry(bridge):
    assert bridge._client_registry.get(bridge.client_id) is not None
    await bridge.close_all()
    assert bridge._client_registry.get(bridge.client_id) is None
    await bridge.close_all()  # no-op, must not raise
    assert bridge._client_registry.get(bridge.client_id) is None


async def test_close_all_default_status_is_completed(bridge):
    await bridge.close_all()
    call_args = bridge.tracker._registry.close.call_args
    assert call_args.args[1] == "completed"


async def test_close_all_status_error_passed_through(bridge):
    await bridge.close_all(status="error")
    call_args = bridge.tracker._registry.close.call_args
    assert call_args.args[1] == "error"


async def test_close_all_second_call_status_does_not_override_first(bridge):
    """First call (e.g. bridge.run()'s own cleanup, status='completed') wins —
    a later no-op call from routes.py's outer finally (status='error') must
    not flip an already-closed session's recorded status."""
    await bridge.close_all(status="completed")
    await bridge.close_all(status="error")
    call_args = bridge.tracker._registry.close.call_args
    assert call_args.args[1] == "completed"


async def test_close_all_step_that_raises_cancelled_error_does_not_abort_teardown(bridge):
    """#101/#221: a teardown step failing with its own CancelledError (here the push
    TTS close) is a cleanup failure, not a cancellation of the caller: close_all()
    finishes (tracker closed, `_closed`) and does not raise. A later call is a no-op."""
    bridge._push_tts = AsyncMock()
    bridge._push_tts.close.side_effect = asyncio.CancelledError()

    await bridge.close_all()

    assert bridge._closed is True
    await bridge.close_all()
    bridge.tracker._registry.close.assert_called_once()


async def test_late_close_of_old_bridge_keeps_reconnected_session(make_bridge, mock_tracker):
    """Issue #113 reconnect race, end to end through JotaBridge.close_all().

    A client disconnects and reconnects: the new session registers a second
    bridge under the SAME client_id, overwriting the old one in ClientRegistry.
    When the old bridge finally tears down (close_all → unregister) it must not
    evict the live new bridge — otherwise the reconnected client silently stops
    receiving push events and status broadcasts.
    """
    registry = ClientRegistry()

    old_bridge, _ = make_bridge(tracker=mock_tracker, client_registry=registry)
    new_bridge, _ = make_bridge(tracker=mock_tracker, client_registry=registry)
    registry.register(old_bridge.client_id, old_bridge)
    registry.register(new_bridge.client_id, new_bridge)  # reconnect overwrites

    # Old bridge tears down late.
    await old_bridge.close_all()

    # The reconnected session survives...
    assert registry.get(new_bridge.client_id) is new_bridge

    # ...and still receives status broadcasts (old bridge's ws must not).
    await registry.broadcast_status("orchestrator", "restored")
    assert await sent(new_bridge) == [
        {"type": "status", "service": "orchestrator", "state": "restored"}
    ]
    assert await sent(old_bridge) == []


async def test_close_all_bounds_wait_on_a_turn_that_ignores_cancellation(bridge, monkeypatch):
    """#115/#221: close_all() cancels the turn in flight; a turn that does not honour
    the cancellation is abandoned after SESSION_TEARDOWN_S, never awaited forever."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 0.05)
    release = asyncio.Event()

    async def _stubborn():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue  # ignores the cancellation

    turn = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)
    bridge._active_turn = turn

    start = time.monotonic()
    await asyncio.wait_for(bridge.close_all(), timeout=2.0)
    assert time.monotonic() - start < 0.5
    assert bridge._closed is True
    release.set()
    await turn


async def test_close_all_cancels_the_turn_in_flight_instead_of_waiting(bridge, monkeypatch):
    """7d / G-23 (#223): close_all() no longer drains a live turn for up to 30 s; it
    cancels it right away."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 30.0)
    turn = asyncio.create_task(asyncio.sleep(3600))
    await asyncio.sleep(0)
    bridge._active_turn = turn

    start = time.monotonic()
    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert time.monotonic() - start < 0.5
    assert turn.cancelled()


async def test_close_all_completes_when_called_from_its_own_tracked_task(bridge):
    """A watchdog task (_idle_watchdog/_transcription_watchdog) that triggers
    its own session's close_all() must not be cancelled by close_all()'s own
    task-cancellation loop — self-cancellation delivers a CancelledError into
    close_all() at its next real suspension point (awaiting the transcriber's
    close(), here), aborting teardown before tracker.close()/registry.close()
    ever run."""

    class _SlowTranscriber:
        async def close(self):
            await asyncio.sleep(0)  # forces a genuine event-loop checkpoint

    bridge.transcriber = _SlowTranscriber()

    async def _self_closing_watchdog():
        await bridge.close_all()

    watchdog_task = asyncio.create_task(_self_closing_watchdog())
    bridge.tasks.append(watchdog_task)

    await asyncio.wait_for(watchdog_task, timeout=1.0)  # must not raise CancelledError

    assert bridge._closed is True
    bridge.tracker._registry.close.assert_called_once()


async def test_close_all_cancels_and_awaits_other_tracked_tasks(bridge):
    """close_all() must not just call .cancel() and move on — it must await
    the cancelled tasks so none are left dangling when it returns."""
    finished = False

    async def _long_task():
        nonlocal finished
        try:
            await asyncio.sleep(10)
        finally:
            finished = True

    task = asyncio.create_task(_long_task())
    bridge.tasks.append(task)

    # Let the task start running before we call close_all()
    await asyncio.sleep(0)

    await bridge.close_all()

    assert task.done()
    assert finished is True


async def test_close_all_finishes_teardown_then_reraises_external_cancellation(bridge):
    """#221: a cancellation delivered to the CALLER while close_all() waits for its
    cancelled session tasks is not swallowed any more. close_all() still finishes every
    step (tracker closed, `_closed`) and then re-raises it.

    close_all() runs in its own task, cancelled by the child from its `finally`
    (mirrors uvicorn/TestClient cancelling the endpoint right after ws.close())."""
    cancelled = False
    closer: asyncio.Task | None = None

    async def _long_watchdog():
        nonlocal cancelled
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled = True
            assert closer is not None
            closer.cancel()

    task = asyncio.create_task(_long_watchdog())
    bridge.tasks.append(task)
    await asyncio.sleep(0)  # let the watchdog reach its await

    closer = asyncio.create_task(bridge.close_all())
    with pytest.raises(asyncio.CancelledError):
        await closer

    assert cancelled is True
    assert bridge._closed is True
    bridge.tracker._registry.close.assert_called_once()
    await bridge.close_all()  # no-op: the safety net of #101 does not repeat the teardown
    bridge.tracker._registry.close.assert_called_once()


# ── #221 4/6: what the client sees when the gateway closes the session ──────


async def test_turn_cancelled_by_close_all_still_sends_its_turn_end(make_bridge):
    """Invariant: exactly one turn_end per turn_start, also when close_all() cancels
    the turn while the socket is still open."""
    b, ws = make_bridge()
    started = asyncio.Event()

    async def stream(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        started.set()
        await asyncio.sleep(3600)

    b.orchestrator.stream_response = stream
    b._active_turn = asyncio.create_task(b._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await asyncio.wait_for(b.close_all(), timeout=2.0)
    await sent(b)

    assert ws.of("turn_start") == [{"type": "turn_start", "turn_id": "t-1", "turn_seq": 1}]
    assert ws.of("turn_end") == [{"type": "turn_end", "turn_id": "t-1"}]


async def test_close_all_sends_turn_end_of_an_open_push_turn(make_bridge):
    """A push turn left open (agent start without its end) gets its turn_end on close."""
    b, ws = make_bridge()
    b.config.push_enabled = True
    await b.on_push_turn_start("sk")

    await b.close_all()
    await sent(b)

    assert [m["turn_id"] for m in ws.of("turn_start")] == ["t-1"]
    assert ws.of("turn_end") == [{"type": "turn_end", "turn_id": "t-1"}]
    assert b._push_turn_open is False


async def test_input_loop_crash_closes_the_session_as_error(make_bridge):
    """G-26: a crash of the input loop ends the session with status `error`."""
    b, ws = make_bridge()

    async def boom():
        raise RuntimeError("receive failed")

    ws.receive = boom
    await asyncio.wait_for(b.run(), timeout=2.0)

    assert b.tracker.close.await_args.kwargs["status"] == "error"
    assert b.tracker.close.await_args.kwargs["reason"] == "error"


async def test_cancelling_run_closes_the_session_as_error_and_propagates(make_bridge):
    """G-26 + #221: an external cancellation of run() closes the session with status
    `error`, finishes the teardown and is re-raised."""
    b, _ = make_bridge()
    runner = asyncio.create_task(b.run())
    await asyncio.sleep(0.05)
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, timeout=2.0)

    assert b._closed is True
    assert b.tracker.close.await_args.kwargs["status"] == "error"


async def test_idle_close_ends_run_normally_as_completed(make_bridge, monkeypatch):
    """The idle watchdog cancels the input loop from close_all(): that is our own
    cancellation, so run() returns normally and the session stays `completed`."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    b, _ = make_bridge()

    await asyncio.wait_for(b.run(), timeout=2.0)

    assert b.tracker.close.await_args.kwargs["status"] == "completed"
    assert b.tracker.close.await_args.kwargs["reason"] == "idle_timeout"


class _SlowCloseTts:
    """TTS stream whose close() takes `close_s` (jota's websockets close_timeout)."""

    def __init__(self, close_s: float) -> None:
        self.close_s = close_s
        self._q: asyncio.Queue = asyncio.Queue()

    async def send_text_chunk(self, t):
        pass

    async def end(self):
        pass

    async def get_audio_stream(self):
        while (item := await self._q.get()) is not None:
            yield item

    async def close(self):
        await asyncio.sleep(self.close_s)


class _TtsProvider:
    name = "fake"

    def __init__(self, stream):
        self.stream = stream

    async def connect(self, **k):
        return self.stream

    def resolve_voice(self, v):
        return v


@pytest.mark.parametrize("close_s", [0.05, 1.0], ids=["tts_rapido", "tts_lento"])
async def test_turn_abandoned_by_close_all_keeps_one_turn_end_before_session_end(
    close_s, make_bridge, monkeypatch
):
    """#221 4/6: when the cancelled turn's TTS cleanup outlasts SESSION_TEARDOWN_S,
    close_all() abandons it but still sends its turn_end before session_end; when
    the cleanup is fast, there is no duplicate."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 0.3)
    b, ws = make_bridge(output_mode=("text", "audio"), tts=_TtsProvider(_SlowCloseTts(close_s)))

    async def tracker_close(status, **meta):
        await b._sender.send_json({"type": "session_end"})

    b.tracker.close.side_effect = tracker_close
    started = asyncio.Event()

    async def stream(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        started.set()
        await asyncio.sleep(3600)

    b.orchestrator.stream_response = stream
    b._active_turn = asyncio.create_task(b._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await b.close_all()
    turn = b._active_turn
    # The abandoned turn finishes its cleanup while routes.py's sender is still open:
    # its finally must not send a second turn_end.
    await asyncio.wait({turn}, timeout=close_s + 1.0)
    assert turn.done()
    await b._sender.aclose(1.0)

    types = ws.types()
    assert ws.of("turn_end") == [{"type": "turn_end", "turn_id": "t-1"}]
    assert types.index("turn_end") < types.index("session_end")


class _StreamingSlowEndTts:
    """TTS whose end() is slow and keeps streaming audio until it finishes."""

    def __init__(self, end_s: float, every: float) -> None:
        self.end_s, self.every = end_s, every
        self.done = False

    async def send_text_chunk(self, t):
        pass

    async def end(self):
        await asyncio.sleep(self.end_s)
        self.done = True

    async def get_audio_stream(self):
        while not self.done:
            await asyncio.sleep(self.every)
            yield b"\x00" * 10

    async def close(self):
        pass


async def test_abandoned_turn_sends_no_audio_after_its_turn_end(make_bridge, monkeypatch):
    """#221 4/6: once close_all() has sent the abandoned turn's turn_end, that turn's
    still-running TTS audio must not reach the client (nothing of a turn after its
    turn_end)."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 0.3)
    b, ws = make_bridge(
        output_mode=("text", "audio"),
        tts=_TtsProvider(_StreamingSlowEndTts(end_s=1.0, every=0.02)),
    )
    class _SlowCloseTranscriber:
        async def close(self):
            await asyncio.sleep(0.2)  # widens the window after turn_end

    b.transcriber = _SlowCloseTranscriber()

    async def tracker_close(status, **meta):
        await b._sender.send_json({"type": "session_end"})

    b.tracker.close.side_effect = tracker_close
    started = asyncio.Event()

    async def stream(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        started.set()
        await asyncio.sleep(3600)

    b.orchestrator.stream_response = stream
    b._active_turn = asyncio.create_task(b._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.sleep(0.05)

    await b.close_all()
    await b._sender.aclose(1.0)
    await asyncio.sleep(1.2)

    labels = [m["type"] if isinstance(m, dict) else "audio" for m in ws.sent]
    assert labels.count("turn_end") == 1
    assert "audio" not in labels[labels.index("turn_end") + 1 :], labels


async def test_close_all_during_paced_audio_ends_promptly(make_bridge, monkeypatch):
    """Foco 3 de 7e: con audio a ritmo en vuelo, close_all no espera al reloj del pacer."""
    from unittest.mock import AsyncMock

    from src.core.config import settings
    from tests.fakes.bridge import tokens

    monkeypatch.setattr(settings, "AUDIO_LEAD_S", 0.1)

    async def audio():
        for _ in range(50):  # 5 s de audio en trozos de 100 ms
            yield b"\x01\x00" * 2400

    tts_client = AsyncMock()
    tts_client.get_audio_stream = audio
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=tts_client)
    b, ws = make_bridge(output_mode=("text", "audio"), tts=tts)
    b.orchestrator.stream_response = tokens("hi")
    b._active_turn = asyncio.create_task(b._call_orchestrator("x"))
    while not any(isinstance(m, bytes) for m in ws.sent):
        await asyncio.sleep(0.01)

    t0 = time.monotonic()
    await asyncio.wait_for(b.close_all(), timeout=settings.SESSION_TEARDOWN_S)
    await sent(b)

    assert time.monotonic() - t0 < settings.SESSION_TEARDOWN_S
    assert len(ws.of("turn_end")) == 1
