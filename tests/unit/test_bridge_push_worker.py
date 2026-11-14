"""#130 S3a: worker FIFO de push por bridge."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from fogar_gateway.core.config import settings
from fogar_gateway.models.schemas import ClientConfig
from tests.fakes.bridge import push, push_reply, sent

SK = "agent:main:hab_sito"


@pytest.fixture
def make_push_bridge(make_bridge):
    def _make(stream=None, connect=None):
        provider = AsyncMock()
        provider.resolve_voice = lambda v: v
        provider.connect = connect or AsyncMock(return_value=stream)
        output_mode = ("audio", "text") if stream is not None or connect is not None else ("text",)
        return make_bridge(
            client_id="hab_sito",
            output_mode=output_mode,
            tts=provider,
            config=ClientConfig(tool_calls_enabled=True),
        )[0]

    return _make


def _workers(bridge):
    return [t for t in bridge.tasks if t.get_name() == "push_worker"]


def _backlog(bridge):
    return bridge.turns.snapshot(0.0)["push_backlog"]


def _summary(msgs):
    out = []
    for m in msgs:
        if isinstance(m, bytes):
            continue
        out.append((m["type"], m.get("text") or m.get("name") or m.get("turn_id")))
    return out


_TOOL = {"phase": "start", "name": "exec", "toolCallId": "c1", "args": {}}


async def test_worker_is_lazy_and_supervised(make_push_bridge):
    bridge = make_push_bridge()
    assert _workers(bridge) == []

    bridge.enqueue_push("chat", {"deltaText": "x"})

    (worker,) = _workers(bridge)
    assert not worker.done()
    await bridge.push_idle()
    await bridge.close_all()
    assert worker.done()


async def test_enqueue_push_runs_events_in_fifo_order_and_is_non_blocking(make_push_bridge):
    bridge = make_push_bridge()
    for event in (*push_reply("a", "b", sk=SK, end=False), ("tool", _TOOL), ("turn_end", SK)):
        bridge.enqueue_push(*event)

    assert _backlog(bridge)["pending"] == 5  # enqueue_push did not run anything synchronously
    await bridge.push_idle()
    assert _summary(await sent(bridge)) == [
        ("turn_start", "t-1"),
        ("token", "a"),
        ("token", "b"),
        ("tool_call", "exec"),
        ("turn_end", "t-1"),
    ]
    assert _backlog(bridge)["pending"] == 0
    await bridge.close_all()


async def test_slow_handler_does_not_block_the_caller_and_keeps_order(make_push_bridge):
    gate = asyncio.Event()
    stream = AsyncMock()

    async def no_audio():
        return
        yield

    async def slow_end():
        await gate.wait()

    stream.get_audio_stream = no_audio
    stream.end = slow_end
    bridge = make_push_bridge(stream=stream)
    await push(bridge, *push_reply("uno", sk=SK, end=False))

    bridge.enqueue_push("turn_end", SK)  # blocks in the TTS end()
    for event in push_reply("dos", sk=SK):
        bridge.enqueue_push(*event)
    await asyncio.sleep(0.02)  # the worker is inside the slow end
    assert _backlog(bridge)["pending"] == 4

    gate.set()
    await bridge.push_idle()
    assert _summary(await sent(bridge)) == [
        ("turn_start", "t-1"),
        ("token", "uno"),
        ("turn_end", "t-1"),
        ("turn_start", "t-2"),
        ("token", "dos"),
        ("turn_end", "t-2"),
    ]
    await bridge.close_all()


async def test_handler_exception_does_not_kill_the_worker(make_push_bridge, caplog):
    bridge = make_push_bridge(connect=AsyncMock(side_effect=RuntimeError("tts exploded")))
    await push(bridge, *push_reply("x", sk=SK))

    assert _summary(await sent(bridge)) == [("turn_start", "t-1"), ("token", "x"), ("turn_end", "t-1")]
    assert not _workers(bridge)[0].done()
    assert _backlog(bridge)["pending"] == 0
    assert "push turn_start falló: RuntimeError" in caplog.text
    assert "tts exploded" not in caplog.text
    await bridge.close_all()


async def test_burst_of_events_keeps_order_and_drains_pending(make_push_bridge):
    bridge = make_push_bridge()
    await push(bridge, *push_reply(*[str(i) for i in range(500)], sk=SK))

    assert [m["text"] for m in await sent(bridge) if m["type"] == "token"] == [str(i) for i in range(500)]
    assert _backlog(bridge)["pending"] == 0
    await bridge.close_all()


async def test_close_all_cancels_worker_before_closing_push_tts(make_push_bridge):
    order: list = []
    blocked = asyncio.Event()
    stream = AsyncMock()

    async def no_audio():
        return
        yield

    async def blocked_end():
        blocked.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("worker_cancelled")
            raise

    async def tts_close():
        order.append("tts_close")

    stream.get_audio_stream = no_audio
    stream.end = blocked_end
    stream.close = tts_close
    bridge = make_push_bridge(stream=stream)
    await push(bridge, ("turn_start", SK))
    bridge.enqueue_push("turn_end", SK)
    await blocked.wait()

    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert order == ["worker_cancelled", "tts_close"]
    assert all(w.done() for w in _workers(bridge))
    assert (await sent(bridge))[-1] == {"type": "turn_end", "turn_id": "t-1"}


async def test_close_all_completes_while_worker_waits_for_audio_drain(make_push_bridge, monkeypatch):
    """Review focus 1: the worker is parked on the audio drain when close_all() cancels
    it — that cancellation must not be swallowed.

    Proof: close_all() finishes within 2s although the worker's audio drain and
    close_all's own worker wait are both capped at 30s (pinned below)."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 30)
    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 30)
    cancelled = asyncio.Event()
    stream = AsyncMock()

    async def stuck_audio():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield b""  # pragma: no cover

    stream.get_audio_stream = stuck_audio
    bridge = make_push_bridge(stream=stream)
    await push(bridge, ("turn_start", SK))
    bridge.enqueue_push("turn_end", SK)
    for _ in range(100):  # until the worker is inside the drain
        if stream.end.await_count:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0)

    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert all(w.done() for w in _workers(bridge))
    assert cancelled.is_set()


async def test_enqueue_push_after_close_is_a_noop(make_push_bridge):
    """Review focus 2: a late frame for an already closed/unregistered bridge."""
    bridge = make_push_bridge()
    await bridge.close_all()

    bridge.enqueue_push("chat", {"deltaText": "late"})

    assert _workers(bridge) == []
    assert _backlog(bridge)["pending"] == 0


async def test_close_all_discards_backlog(make_push_bridge):
    gate = asyncio.Event()

    async def slow_connect(**_):
        await gate.wait()

    bridge = make_push_bridge(connect=AsyncMock(side_effect=slow_connect))
    bridge.enqueue_push("turn_start", SK)
    bridge.enqueue_push("chat", {"deltaText": "never"})
    await asyncio.sleep(0.01)

    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert [m["type"] for m in await sent(bridge)] == ["turn_start", "turn_end"]
    assert _backlog(bridge)["pending"] == 0
    await asyncio.wait_for(bridge.push_idle(), timeout=1.0)  # nothing left unfinished


async def test_run_ends_when_client_disconnects_even_if_push_worker_spawned_first(make_push_bridge):
    """F1: a push arriving between connect_internal_services() and run() spawns the
    worker into self.tasks[0]; run() must still await the client input loop."""
    bridge = make_push_bridge()
    bridge.client_ws.inbox.put_nowait({"type": "websocket.disconnect"})
    bridge.enqueue_push("chat", {"deltaText": "x"})

    loop = asyncio.get_running_loop()
    started = loop.time()
    # run() swallows the CancelledError wait_for injects on timeout (it then closes
    # and returns), so a hang would not raise TimeoutError: assert on elapsed time.
    await asyncio.wait_for(bridge.run(), timeout=2.0)

    assert loop.time() - started < 1.0
    assert bridge._closed is True


async def test_push_pending_peak(make_push_bridge):
    gate = asyncio.Event()

    async def slow_connect(**_):
        await gate.wait()

    bridge = make_push_bridge(connect=AsyncMock(side_effect=slow_connect))
    bridge.enqueue_push("turn_start", SK)
    await asyncio.sleep(0.01)  # the worker holds the first event
    for _ in range(3):
        bridge.enqueue_push("chat", {"deltaText": "x"})
    assert _backlog(bridge) == {"pending": 4, "peak": 4}
    gate.set()
    await bridge.push_idle()
    assert _backlog(bridge) == {"pending": 0, "peak": 4}
    await bridge.close_all()
