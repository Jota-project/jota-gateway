"""#130 S3a: worker FIFO de push por bridge."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.config import settings
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry


def make_bridge():
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=None)
    return JotaBridge(
        client=client,
        config=ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=tts,
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )


def _record_executors(bridge, order):
    async def start(sk):
        order.append(("start", sk))

    async def chat(payload):
        order.append(("chat", payload["deltaText"]))

    async def tool(data):
        order.append(("tool", data["name"]))

    async def end(sk):
        order.append(("end", sk))

    bridge.on_push_turn_start = start
    bridge.deliver_push = chat
    bridge.deliver_push_tool_call = tool
    bridge.on_push_turn_end = end


@pytest.mark.asyncio
async def test_worker_is_lazy_and_supervised():
    bridge = make_bridge()
    assert bridge._push_worker is None
    assert not [t for t in bridge.tasks if t.get_name() == "push_worker"]

    bridge.enqueue_push("chat", {"deltaText": "x"})

    assert bridge._push_worker is not None
    assert bridge._push_worker in bridge.tasks
    assert bridge._push_worker.get_name() == "push_worker"
    await bridge.push_idle()
    await bridge.close_all()


@pytest.mark.asyncio
async def test_enqueue_push_runs_events_in_fifo_order_and_is_non_blocking():
    bridge = make_bridge()
    order: list = []
    _record_executors(bridge, order)

    bridge.enqueue_push("turn_start", "sk")
    bridge.enqueue_push("chat", {"deltaText": "a"})
    bridge.enqueue_push("chat", {"deltaText": "b"})
    bridge.enqueue_push("tool", {"name": "exec"})
    bridge.enqueue_push("turn_end", "sk")

    assert order == []  # enqueue_push did not run anything synchronously
    await bridge.push_idle()
    assert order == [
        ("start", "sk"),
        ("chat", "a"),
        ("chat", "b"),
        ("tool", "exec"),
        ("end", "sk"),
    ]
    assert bridge._push_pending == 0
    assert bridge._push_pending_since is None
    await bridge.close_all()


@pytest.mark.asyncio
async def test_slow_handler_does_not_block_the_caller_and_keeps_order():
    bridge = make_bridge()
    order: list = []
    _record_executors(bridge, order)
    gate = asyncio.Event()

    async def slow_end(sk):
        await gate.wait()
        order.append(("end", sk))

    bridge.on_push_turn_end = slow_end

    bridge.enqueue_push("turn_end", "sk")
    bridge.enqueue_push("chat", {"deltaText": "after"})
    await asyncio.sleep(0)  # worker starts and blocks inside slow_end
    assert order == []
    assert bridge._push_pending == 2

    gate.set()
    await bridge.push_idle()
    assert order == [("end", "sk"), ("chat", "after")]
    await bridge.close_all()


@pytest.mark.asyncio
async def test_handler_exception_does_not_kill_the_worker():
    bridge = make_bridge()
    order: list = []
    _record_executors(bridge, order)

    async def boom(payload):
        raise RuntimeError("tts exploded")

    bridge.deliver_push = boom

    bridge.enqueue_push("chat", {"deltaText": "x"})
    bridge.enqueue_push("turn_end", "sk")
    await bridge.push_idle()

    assert order == [("end", "sk")]
    assert not bridge._push_worker.done()
    assert bridge._push_pending == 0
    await bridge.close_all()


@pytest.mark.asyncio
async def test_burst_of_events_keeps_order_and_drains_pending():
    bridge = make_bridge()
    order: list = []
    _record_executors(bridge, order)

    for i in range(500):
        bridge.enqueue_push("chat", {"deltaText": str(i)})
    await bridge.push_idle()

    assert order == [("chat", str(i)) for i in range(500)]
    assert bridge._push_pending == 0
    await bridge.close_all()


@pytest.mark.asyncio
async def test_close_all_cancels_worker_before_closing_push_tts():
    bridge = make_bridge()
    order: list = []
    mock_tts = AsyncMock()

    async def tts_close():
        order.append("tts_close")

    mock_tts.close = tts_close
    bridge._push_tts = mock_tts
    blocked = asyncio.Event()

    async def blocked_end(sk):
        blocked.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("worker_cancelled")
            raise

    bridge.on_push_turn_end = blocked_end
    bridge.enqueue_push("turn_end", "sk")
    await blocked.wait()

    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert order == ["worker_cancelled", "tts_close"]
    assert bridge._push_worker.done()


@pytest.mark.asyncio
async def test_close_all_completes_while_worker_waits_for_audio_drain(monkeypatch):
    """Review focus 1: the real on_push_turn_end is parked on the audio task when
    close_all() cancels the worker — that cancellation must not be swallowed.

    Proof: close_all() finishes within 2s although the worker's audio drain and
    close_all's own worker wait are both capped at 30s (pinned below)."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 30)
    monkeypatch.setattr(settings, "SHUTDOWN_DRAIN_S", 30)
    bridge = make_bridge()
    bridge._push_turn_open = True
    bridge._push_turn_id = "t-1"
    bridge._push_tts = AsyncMock()
    stuck = asyncio.create_task(asyncio.sleep(3600))
    bridge._push_audio_task = stuck

    bridge.enqueue_push("turn_end", "sk")
    # Wait (bounded) until the worker dequeued the event and is inside the real
    # on_push_turn_end; the queue being empty means it was picked up.
    for _ in range(100):
        if bridge._push_queue.empty():
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0)

    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert bridge._push_worker.done()
    assert stuck.cancelled()


@pytest.mark.asyncio
async def test_enqueue_push_after_close_is_a_noop():
    """Review focus 2: a late frame for an already closed/unregistered bridge."""
    bridge = make_bridge()
    await bridge.close_all()

    bridge.enqueue_push("chat", {"deltaText": "late"})

    assert bridge._push_worker is None
    assert bridge._push_pending == 0
    assert bridge._push_queue.empty()


@pytest.mark.asyncio
async def test_close_all_discards_backlog():
    bridge = make_bridge()
    order: list = []
    _record_executors(bridge, order)
    gate = asyncio.Event()

    async def slow_start(sk):
        await gate.wait()

    bridge.on_push_turn_start = slow_start
    bridge.enqueue_push("turn_start", "sk")
    bridge.enqueue_push("chat", {"deltaText": "never"})
    await asyncio.sleep(0)

    await asyncio.wait_for(bridge.close_all(), timeout=2.0)

    assert order == []
    assert bridge._push_pending == 0
    assert bridge._push_queue.empty()
    await asyncio.wait_for(bridge.push_idle(), timeout=1.0)  # nothing left unfinished


@pytest.mark.asyncio
async def test_run_ends_when_client_disconnects_even_if_push_worker_spawned_first():
    """F1: a push arriving between connect_internal_services() and run() spawns the
    worker into self.tasks[0]; run() must still await the client input loop."""
    bridge = make_bridge()
    bridge.client_ws.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
    bridge.enqueue_push("chat", {"deltaText": "x"})

    loop = asyncio.get_running_loop()
    started = loop.time()
    # run() swallows the CancelledError wait_for injects on timeout (it then closes
    # and returns), so a hang would not raise TimeoutError: assert on elapsed time.
    await asyncio.wait_for(bridge.run(), timeout=2.0)

    assert loop.time() - started < 1.0
    assert bridge._closed is True
