"""#130 S3a: worker FIFO de push por bridge."""

import asyncio
from unittest.mock import AsyncMock

import pytest

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
