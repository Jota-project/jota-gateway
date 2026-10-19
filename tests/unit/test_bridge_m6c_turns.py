"""M6c (#215, #201): turn lifecycle patches in JotaBridge.

G-08 (#201) cancel race, G-09 turn created during close_all, G-10 one turn_end per
turn on every path, G-11 turn_seq wraparound, G-18 gone client cancels its turn.
Plus the two M6 exit-gate mutants: the `_closed` guard of close_all and the `>=`
boundary of barge_in_min_chars.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

from src.core.config import settings
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.outbound import QueuedSender
from src.services.protocol import OrchestratorEvent


class RecWS:
    """Records everything sent to the client (json dicts and audio bytes)."""

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


def _tracker():
    t = MagicMock()
    t.record = AsyncMock()
    t.close = AsyncMock()
    return t


def _bridge(ws=None, *, output_mode=("text",), config=None, input_mode="text", sender=None, tts=None):
    ws = ws or RecWS()
    b = JotaBridge(
        client=Client(id="u", client_key="k", is_active=True),
        config=config or ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=tts or AsyncMock(),
        tracker=_tracker(),
        handshake=Handshake(client_key="k", input_mode=input_mode, output_mode=list(output_mode)),
        client_registry=ClientRegistry(),
        default_agent="main",
        sender=sender,
    )
    b.transcriber = None
    return b, ws


def _tokens(*tokens):
    async def _s(*a, **k):
        for t in tokens:
            yield OrchestratorEvent(type="token", content=t)

    return _s


def _blocking(started: asyncio.Event | None = None):
    """A stream that emits one token then waits until cancelled."""

    async def _s(*a, **k):
        yield OrchestratorEvent(type="token", content="hi")
        if started:
            started.set()
        await asyncio.sleep(3600)

    return _s


async def _wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


# ── G-08 (#201) ──────────────────────────────────────────────────────────────


async def test_concurrent_cancels_do_not_lose_a_new_turn():
    bridge, _ = _bridge()
    gate = asyncio.Event()

    async def old():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await gate.wait()  # slow cancellation: both callers end up waiting on it
            raise

    bridge._active_turn = asyncio.create_task(old())
    await asyncio.sleep(0)
    new_task = None

    async def caller_a():
        nonlocal new_task
        await bridge._cancel_active_turn()
        # another path starts a new turn right after A's cancel returns
        new_task = asyncio.create_task(asyncio.sleep(3600))
        bridge._active_turn = new_task

    async def caller_b():
        return await bridge._cancel_active_turn()

    ta = asyncio.create_task(caller_a())
    tb = asyncio.create_task(caller_b())
    await asyncio.sleep(0.02)
    gate.set()
    await asyncio.gather(ta, tb)

    try:
        assert bridge._active_turn is new_task, "the new turn's reference was lost"
        assert not new_task.cancelled()
    finally:
        new_task.cancel()


# ── G-09 ─────────────────────────────────────────────────────────────────────


async def test_send_during_close_all_does_not_start_or_replace_a_turn(monkeypatch):
    monkeypatch.setattr(settings, "SHUTDOWN_DRAIN_S", 5.0)
    bridge, ws = _bridge()
    release = asyncio.Event()
    calls = []

    async def stream(*a, **k):
        calls.append(1)
        await release.wait()
        yield OrchestratorEvent(type="token", content="x")

    bridge.orchestrator.stream_response = stream
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("one"))
    await _wait_for(lambda: len(calls) == 1)
    first = bridge._active_turn

    closer = asyncio.create_task(bridge.close_all())
    await asyncio.sleep(0.05)  # close_all is now draining the turn
    ws.inbox.put_nowait({"type": "websocket.message", "text": json.dumps({"type": "send", "text": "two"})})
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await bridge._client_input_loop()

    assert len(calls) == 1, "a turn was created while the session was closing"
    assert not first.cancelled(), "the draining turn was cancelled by a new send"
    release.set()
    await asyncio.wait_for(closer, timeout=2.0)
    assert bridge._active_turn is None or bridge._active_turn.done()


async def test_close_all_twice_closes_microservices_once():
    """Regression guard for the `_closed` guard (M6 exit-gate mutant 3)."""
    bridge, _ = _bridge()
    bridge.transcriber = MagicMock()
    bridge.transcriber.close = AsyncMock()

    await bridge.close_all()
    await bridge.close_all()

    assert bridge._closed is True
    assert bridge.transcriber.close.await_count == 1
    assert bridge.tracker.close.await_count == 1


# ── barge-in boundary (M6 exit-gate mutant 4) ────────────────────────────────


async def _barge(len_text, min_chars=5):
    bridge, ws = _bridge(config=ClientConfig(barge_in_enabled=True, barge_in_min_chars=min_chars))
    bridge._active_turn = asyncio.create_task(asyncio.sleep(3600))
    await asyncio.sleep(0)
    await bridge._on_transcription("a" * len_text, False)
    return bridge, ws


async def test_barge_in_fires_at_exactly_min_chars():
    bridge, ws = await _barge(5)
    assert bridge._active_turn is None
    assert ws.of("interrupted")


async def test_barge_in_does_not_fire_one_char_below_min():
    bridge, ws = await _barge(4)
    assert bridge._active_turn is not None and not bridge._active_turn.done()
    assert not ws.of("interrupted")
    bridge._active_turn.cancel()


# ── G-10 ─────────────────────────────────────────────────────────────────────


async def test_normal_turn_sends_exactly_one_turn_end():
    bridge, ws = _bridge()
    bridge.orchestrator.stream_response = _tokens("a", "b")
    await bridge._call_orchestrator("x")
    assert ws.types() == ["turn_start", "token", "token", "turn_end"]


async def test_runtime_error_turn_sends_error_and_one_turn_end():
    bridge, ws = _bridge()

    async def s(*a, **k):
        yield OrchestratorEvent(type="error", content="orch down")

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")
    assert ws.types() == ["turn_start", "error", "turn_end"]


async def test_unexpected_exception_turn_sends_error_and_turn_end():
    bridge, ws = _bridge()

    async def s(*a, **k):
        raise ValueError("secret user text")
        yield  # pragma: no cover

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")

    assert ws.types() == ["turn_start", "error", "turn_end"]
    err = ws.of("error")[0]
    assert err["code"] == "TURN_ERROR" and err["fatal"] is False and err["turn_id"] == "t-1"
    assert "secret" not in json.dumps(err)


async def test_cancelled_turn_sends_turn_end():
    bridge, ws = _bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = _blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await bridge._cancel_active_turn()

    assert ws.types().count("turn_end") == 1
    assert ws.of("turn_end")[0]["turn_id"] == "t-1"


async def test_cancel_message_sends_turn_end():
    bridge, ws = _bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = _blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)
    ws.inbox.put_nowait({"type": "websocket.message", "text": json.dumps({"type": "cancel"})})
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await bridge._client_input_loop()

    assert ws.types().count("turn_end") == 1


async def test_send_that_overrides_a_turn_closes_it_with_one_turn_end_each():
    bridge, ws = _bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = _blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)
    bridge.orchestrator.stream_response = _tokens("new")
    ws.inbox.put_nowait({"type": "websocket.message", "text": json.dumps({"type": "send", "text": "y"})})
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await bridge._client_input_loop()
    await asyncio.wait_for(bridge._active_turn, 2)

    ends = ws.of("turn_end")
    assert [e["turn_id"] for e in ends] == ["t-1", "t-2"]
    # t-1 closes before t-2 opens
    order = [(m["type"], m.get("turn_id")) for m in ws.sent if isinstance(m, dict)
             if m["type"] in ("turn_start", "turn_end")]
    assert order == [("turn_start", "t-1"), ("turn_end", "t-1"), ("turn_start", "t-2"), ("turn_end", "t-2")]


async def test_final_transcription_cancelling_a_turn_sends_turn_end():
    bridge, ws = _bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = _blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await bridge._on_transcription("hola que tal", True)

    assert ws.types().count("turn_end") == 1


async def test_barge_in_sends_turn_end_then_interrupted_with_turn_id():
    bridge, ws = _bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = _blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await bridge._on_transcription("hola que tal", False)

    tail = [m for m in ws.sent if isinstance(m, dict) and m["type"] in ("turn_end", "interrupted")]
    assert tail == [
        {"type": "turn_end", "turn_id": "t-1"},
        {"type": "interrupted", "turn_id": "t-1"},
    ]


# ── G-11 ─────────────────────────────────────────────────────────────────────


def _tts_with_one_chunk():
    tts_client = AsyncMock()

    async def audio():
        yield b"\x01\x02"

    tts_client.get_audio_stream = audio
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=tts_client)
    return tts


async def test_turn_seq_wraps_in_audio_header_instead_of_overflowing():
    bridge, ws = _bridge(output_mode=("text", "audio"), tts=_tts_with_one_chunk())
    bridge.orchestrator.stream_response = _tokens("a")
    bridge._turn_seq = 65535

    await bridge._call_orchestrator("x")

    frames = [m for m in ws.sent if isinstance(m, bytes)]
    assert frames == [b"\xa1\x00\x00\x01\x02"]
    start = ws.of("turn_start")[0]
    assert start["turn_id"] == "t-65536" and start["turn_seq"] == 0  # same value as the header


async def test_push_turn_seq_wraps_in_audio_header():
    bridge, ws = _bridge(output_mode=("text", "audio"), tts=_tts_with_one_chunk())
    bridge._turn_seq = 65535

    await bridge.on_push_turn_start("sk")
    await asyncio.sleep(0.05)

    frames = [m for m in ws.sent if isinstance(m, bytes)]
    assert frames == [b"\xa1\x00\x00\x01\x02"]
    assert ws.of("turn_start")[0]["turn_seq"] == 0
    await bridge.close_all()


# ── G-18 ─────────────────────────────────────────────────────────────────────


async def test_disconnected_client_cancels_its_turn_instead_of_draining(monkeypatch):
    monkeypatch.setattr(settings, "SHUTDOWN_DRAIN_S", 30.0)
    bridge, ws = _bridge()
    started = asyncio.Event()
    closed = asyncio.Event()

    async def s(*a, **k):
        try:
            yield OrchestratorEvent(type="token", content="hi")
            started.set()
            await asyncio.sleep(3600)
        finally:
            closed.set()

    bridge.orchestrator.stream_response = s
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await bridge._client_input_loop()
    t0 = time.monotonic()
    await asyncio.wait_for(bridge.close_all(), timeout=3.0)

    assert time.monotonic() - t0 < 2.0
    assert closed.is_set(), "the orchestrator stream was left open"


async def test_client_gone_during_a_turn_cancels_the_orchestrator_stream():
    failing_ws = AsyncMock()
    failing_ws.send_json = AsyncMock(side_effect=RuntimeError("socket closed"))
    failing_ws.send_bytes = AsyncMock(side_effect=RuntimeError("socket closed"))
    sender = QueuedSender(failing_ws)
    bridge, _ = _bridge(sender=sender)
    closed = asyncio.Event()

    async def s(*a, **k):
        try:
            while True:
                yield OrchestratorEvent(type="token", content="t")
                await asyncio.sleep(0.01)
        finally:
            closed.set()

    bridge.orchestrator.stream_response = s
    turn = asyncio.create_task(bridge._call_orchestrator("x"))
    try:
        await asyncio.wait_for(asyncio.shield(turn), timeout=2.0)
    except TimeoutError:
        turn.cancel()
        raise AssertionError("the turn kept running for a client that is gone") from None
    finally:
        await sender.aclose(1.0)
    assert closed.is_set()


async def test_close_all_starting_while_a_send_waits_for_the_cancel_leaves_no_turn(monkeypatch):
    """G-09 (review): `_start_turn` must re-check `_closing` after awaiting the
    cancellation of the previous turn, inside the lock."""
    monkeypatch.setattr(settings, "SHUTDOWN_DRAIN_S", 5.0)
    bridge, _ = _bridge()
    gate = asyncio.Event()
    new_turns = []

    async def old():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await gate.wait()  # slow cancellation
            raise

    async def stream(*a, **k):
        new_turns.append(1)
        await asyncio.sleep(3600)
        yield OrchestratorEvent(type="token", content="x")

    bridge.orchestrator.stream_response = stream
    bridge._active_turn = asyncio.create_task(old())
    await asyncio.sleep(0)

    starter = asyncio.create_task(bridge._start_turn("two"))
    await asyncio.sleep(0.02)  # _start_turn is now awaiting the old turn's cancellation
    closer = asyncio.create_task(bridge.close_all())
    await asyncio.sleep(0.02)  # close_all has begun
    gate.set()
    await asyncio.wait_for(asyncio.gather(starter, closer), timeout=3.0)

    live = bridge._active_turn is not None and not bridge._active_turn.done()
    if live:
        bridge._active_turn.cancel()
    assert not live, "a turn is alive after close_all() completed"
    assert new_turns == []
