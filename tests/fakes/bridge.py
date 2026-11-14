"""One way to build a FogarBridge in tests (#270).

The bridge sends through a real `QueuedSender`, the production path: what a
test reads from `RecWS.sent` is what a client would have received. Writes
happen on the sender's own task, so read them with `await sent(bridge)`, which
drains the queue first.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from fogar_gateway.models.schemas import Client, ClientConfig, Handshake
from fogar_gateway.services.bridge import FogarBridge
from fogar_gateway.services.openclaw.registry import ClientRegistry
from fogar_gateway.services.outbound import ClientGone, QueuedSender
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.fakes.speech import speech_providers


class RecWS:
    """A client socket that records everything sent to it (json dicts and audio bytes)."""

    def __init__(self):
        self.sent: list = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def send_json(self, p):
        self.sent.append(p)

    async def send_bytes(self, b):
        self.sent.append(b)

    async def receive(self):
        return await self.inbox.get()

    def types(self):
        return [m["type"] for m in self.sent if isinstance(m, dict)]

    def of(self, type_):
        return [m for m in self.sent if isinstance(m, dict) and m["type"] == type_]


def fake_tracker():
    t = MagicMock()
    t.record = AsyncMock()
    t.close = AsyncMock()
    return t


def make_bridge(
    ws=None,
    *,
    client_id="u",
    output_mode=("text",),
    input_mode="text",
    tts=None,
    stt=None,
    transcriber=None,
    register=False,
    **overrides,
):
    """Return `(bridge, ws)`. Any `FogarBridge` argument can be overridden by name.

    Must run inside the event loop (the `QueuedSender` starts its writer task).
    `register=True` adds the bridge to its `client_registry`, as `routes.py` does.
    """
    ws = ws if ws is not None else RecWS()
    kwargs = dict(
        client=Client(id=client_id, client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(tts=tts, stt=stt),
        tracker=fake_tracker(),
        handshake=Handshake(client_key="k", input_mode=input_mode, output_mode=list(output_mode)),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    kwargs.update(overrides)
    if "sender" not in kwargs:  # not setdefault: it would start a writer task nobody closes
        kwargs["sender"] = QueuedSender(ws)
    bridge = FogarBridge(**kwargs)
    bridge.transcriber = transcriber
    if register:
        kwargs["client_registry"].register(bridge.client_id, bridge)
    return bridge, ws


async def sent(bridge) -> list:
    """Everything the client has received so far, after draining the sender.

    Raises `ClientGone` if the sender broke: an empty list must mean "nothing was
    sent", not "the sender dropped it". With a broken sender, read `ws.sent`."""
    await bridge._sender.flush()
    return bridge.client_ws.sent


async def start_turn(bridge, text: str = "x") -> asyncio.Task:
    """Start a user turn as a `send` does (replacing the one in flight); return its task."""
    await bridge.turns.start_user_turn(text)
    task = bridge.turns.user_task
    assert task is not None, "the turn did not start"
    return task


async def run_turn(bridge, text: str = "x", timeout: float = 5.0) -> None:
    """Run one user turn to its end (its turn_end included)."""
    task = await start_turn(bridge, text)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        raise AssertionError(f"the turn did not end in {timeout}s")


async def push(bridge, *events) -> None:
    """Feed OpenClaw push events `(kind, arg)` through the bridge's push worker and wait
    until it handled them all (what FrameDispatcher does with `enqueue_push`)."""
    for kind, arg in events:
        bridge.enqueue_push(kind, arg)
    await bridge.push_idle()


def push_reply(*deltas: str, sk: str = "sk", end: bool = True) -> list[tuple[str, object]]:
    """The events of a push reply: agent start, one `chat` per delta and (if `end`) agent end."""
    events: list[tuple[str, object]] = [("turn_start", sk)]
    events += [("chat", {"sessionKey": sk, "deltaText": d}) for d in deltas]
    if end:
        events.append(("turn_end", sk))
    return events


def tokens(*tokens):
    """An orchestrator `stream_response` that yields these tokens and ends."""

    async def _s(*a, **k):
        for t in tokens:
            yield OrchestratorEvent(type="token", content=t)

    return _s


def blocking(started: asyncio.Event | None = None):
    """An orchestrator `stream_response` that emits one token then waits until cancelled."""

    async def _s(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        if started:
            started.set()
        await asyncio.sleep(3600)

    return _s


async def wait_until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


class GoneSender:
    """A sender whose client already left: every send raises `ClientGone`, as a broken
    `QueuedSender` does. Use it as `make_bridge(sender=GoneSender())`; pass another
    exception to reach the bridge's generic send-failure branches."""

    def __init__(self, error: type[Exception] = ClientGone):
        self._error = error

    async def send_json(self, payload: dict) -> None:
        raise self._error("cliente no disponible")

    async def send_bytes(self, data: bytes) -> None:
        raise self._error("cliente no disponible")

    async def flush(self) -> None:
        raise self._error("cliente no disponible")

    async def aclose(self, timeout: float) -> None:
        return None

    def queue_stats(self) -> dict:
        return {"pending_msgs": 0, "pending_bytes": 0, "peak_msgs": 0, "peak_bytes": 0}


class DirectSender:
    """Passthrough: writes straight to the socket, no queue. For tests of the
    `PipelineTracker` alone; the bridge uses the real `QueuedSender`."""

    def __init__(self, ws):
        self._ws = ws

    async def send_json(self, payload: dict) -> None:
        await self._ws.send_json(payload)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def flush(self) -> None:
        return None

    async def aclose(self, timeout: float) -> None:
        return None

    def queue_stats(self) -> dict:
        return {"pending_msgs": 0, "pending_bytes": 0, "peak_msgs": 0, "peak_bytes": 0}
