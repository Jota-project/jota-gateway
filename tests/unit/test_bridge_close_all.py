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
from unittest.mock import AsyncMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.outbound import QueuedSender
from src.services.protocol import OrchestratorEvent
from tests.fakes.speech import speech_providers
from tests.unit.test_bridge_m6c_turns import RecWS, _bridge

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


@pytest.fixture
def bridge(mock_tracker):
    ws = AsyncMock()
    registry = ClientRegistry()
    b = JotaBridge(
        client=_CLIENT,
        config=_CONFIG,
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
        client_registry=registry,
        default_agent="main",
    )
    registry.register(b.client_id, b)
    return b


async def test_close_all_twice_does_not_raise(bridge):
    await bridge.close_all()
    await bridge.close_all()


async def test_close_all_twice_only_records_one_session_end_event(bridge):
    await bridge.close_all()
    await bridge.close_all()
    session_end_events = [e for e in bridge.tracker.events if e.stage == "session_end"]
    assert len(session_end_events) == 1


async def test_close_all_twice_only_calls_registry_close_once(bridge):
    await bridge.close_all()
    await bridge.close_all()
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


async def test_late_close_of_old_bridge_keeps_reconnected_session(mock_tracker):
    """Issue #113 reconnect race, end to end through JotaBridge.close_all().

    A client disconnects and reconnects: the new session registers a second
    bridge under the SAME client_id, overwriting the old one in ClientRegistry.
    When the old bridge finally tears down (close_all → unregister) it must not
    evict the live new bridge — otherwise the reconnected client silently stops
    receiving push events and status broadcasts.
    """
    registry = ClientRegistry()

    def _make_bridge():
        return JotaBridge(
            client=_CLIENT,
            config=_CONFIG,
            client_ws=AsyncMock(),
            orchestrator=AsyncMock(),
            speech=speech_providers(),
            tracker=mock_tracker,
            handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
            client_registry=registry,
            default_agent="main",
        )

    old_bridge = _make_bridge()
    new_bridge = _make_bridge()
    registry.register(old_bridge.client_id, old_bridge)
    registry.register(new_bridge.client_id, new_bridge)  # reconnect overwrites

    # Old bridge tears down late.
    await old_bridge.close_all()

    # The reconnected session survives...
    assert registry.get(new_bridge.client_id) is new_bridge

    # ...and still receives status broadcasts (old bridge's ws must not).
    await registry.broadcast_status("orchestrator", "restored")
    new_bridge.client_ws.send_json.assert_awaited_once_with(
        {"type": "status", "service": "orchestrator", "state": "restored"}
    )
    old_bridge.client_ws.send_json.assert_not_awaited()


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


async def test_turn_cancelled_by_close_all_still_sends_its_turn_end():
    """Invariant: exactly one turn_end per turn_start, also when close_all() cancels
    the turn while the socket is still open."""
    ws = RecWS()
    sender = QueuedSender(ws)
    b, _ = _bridge(ws, sender=sender)
    started = asyncio.Event()

    async def stream(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        started.set()
        await asyncio.sleep(3600)

    b.orchestrator.stream_response = stream
    b._active_turn = asyncio.create_task(b._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await asyncio.wait_for(b.close_all(), timeout=2.0)
    await sender.aclose(1.0)

    assert ws.of("turn_start") == [{"type": "turn_start", "turn_id": "t-1", "turn_seq": 1}]
    assert ws.of("turn_end") == [{"type": "turn_end", "turn_id": "t-1"}]


async def test_close_all_sends_turn_end_of_an_open_push_turn():
    """A push turn left open (agent start without its end) gets its turn_end on close."""
    ws = RecWS()
    sender = QueuedSender(ws)
    b, _ = _bridge(ws, sender=sender)
    b.config.push_enabled = True
    await b.on_push_turn_start("sk")

    await b.close_all()
    await sender.aclose(1.0)

    assert [m["turn_id"] for m in ws.of("turn_start")] == ["t-1"]
    assert ws.of("turn_end") == [{"type": "turn_end", "turn_id": "t-1"}]
    assert b._push_turn_open is False


async def test_input_loop_crash_closes_the_session_as_error():
    """G-26: a crash of the input loop ends the session with status `error`."""
    b, ws = _bridge()

    async def boom():
        raise RuntimeError("receive failed")

    ws.receive = boom
    await asyncio.wait_for(b.run(), timeout=2.0)

    assert b.tracker.close.await_args.kwargs["status"] == "error"
    assert b.tracker.close.await_args.kwargs["reason"] == "error"


async def test_cancelling_run_closes_the_session_as_error_and_propagates():
    """G-26 + #221: an external cancellation of run() closes the session with status
    `error`, finishes the teardown and is re-raised."""
    b, _ = _bridge()
    runner = asyncio.create_task(b.run())
    await asyncio.sleep(0.05)
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, timeout=2.0)

    assert b._closed is True
    assert b.tracker.close.await_args.kwargs["status"] == "error"


async def test_idle_close_ends_run_normally_as_completed(monkeypatch):
    """The idle watchdog cancels the input loop from close_all(): that is our own
    cancellation, so run() returns normally and the session stays `completed`."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    b, _ = _bridge()

    await asyncio.wait_for(b.run(), timeout=2.0)

    assert b.tracker.close.await_args.kwargs["status"] == "completed"
    assert b.tracker.close.await_args.kwargs["reason"] == "idle_timeout"
