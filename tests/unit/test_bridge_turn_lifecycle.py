"""Turn lifecycle in FogarBridge (#215, #201).

G-08 (#201) cancel race, G-09 turn created during close_all, G-10 one turn_end per
turn on every path, G-11 turn_seq wraparound, G-18 gone client cancels its turn.
The M6 exit-gate mutants live with their topic: the `_closed` guard of close_all in
test_bridge_close_all.py, the `>=` boundary of barge_in_min_chars in test_bridge_barge_in.py.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock

from fogar_gateway.core.config import settings
from fogar_gateway.services.outbound import QueuedSender
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.fakes.bridge import blocking, sent, tokens, wait_until

# ── G-08 (#201) ──────────────────────────────────────────────────────────────


async def test_concurrent_cancels_do_not_lose_a_new_turn(make_bridge):
    bridge, _ = make_bridge()
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


async def test_send_during_close_all_does_not_start_or_replace_a_turn(make_bridge, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 5.0)
    bridge, ws = make_bridge()
    release = asyncio.Event()
    calls = []

    async def stream(*a, **k):
        calls.append(1)
        await release.wait()
        yield OrchestratorEvent(type="token", content="x")

    bridge.orchestrator.stream_response = stream
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("one"))
    await wait_until(lambda: len(calls) == 1)
    first = bridge._active_turn

    closer = asyncio.create_task(bridge.close_all())
    await asyncio.sleep(0)  # close_all has started (_closing is set)
    ws.inbox.put_nowait({"type": "websocket.message", "text": json.dumps({"type": "send", "text": "two"})})
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await bridge._client_input_loop()
    await asyncio.wait_for(closer, timeout=2.0)

    assert len(calls) == 1, "a turn was created while the session was closing"
    # 7d (#223): close_all cancels the turn itself; the send neither replaced it
    # nor started another one.
    assert bridge._active_turn is first
    assert first.cancelled()
    await sent(bridge)
    assert ws.of("turn_end") == [{"type": "turn_end", "turn_id": "t-1"}]


# ── G-10 ─────────────────────────────────────────────────────────────────────


async def test_normal_turn_sends_exactly_one_turn_end(make_bridge):
    bridge, ws = make_bridge()
    bridge.orchestrator.stream_response = tokens("a", "b")
    await bridge._call_orchestrator("x")
    await sent(bridge)
    assert ws.types() == ["turn_start", "token", "token", "turn_end"]


async def test_runtime_error_turn_sends_error_and_one_turn_end(make_bridge):
    bridge, ws = make_bridge()

    async def s(*a, **k):
        yield OrchestratorEvent(type="error", content="orch down")

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")
    await sent(bridge)
    assert ws.types() == ["turn_start", "error", "turn_end"]


async def test_unexpected_exception_turn_sends_error_and_turn_end(make_bridge):
    bridge, ws = make_bridge()

    async def s(*a, **k):
        raise ValueError("secret user text")
        yield  # pragma: no cover

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")

    await sent(bridge)
    assert ws.types() == ["turn_start", "error", "turn_end"]
    err = ws.of("error")[0]
    assert err["code"] == "TURN_ERROR" and err["fatal"] is False and err["turn_id"] == "t-1"
    assert "secret" not in json.dumps(err)


async def test_cancelled_turn_sends_turn_end(make_bridge):
    bridge, ws = make_bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await bridge._cancel_active_turn()

    await sent(bridge)
    assert ws.types().count("turn_end") == 1
    assert ws.of("turn_end")[0]["turn_id"] == "t-1"


async def test_cancel_message_sends_turn_end(make_bridge):
    bridge, ws = make_bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)
    ws.inbox.put_nowait({"type": "websocket.message", "text": json.dumps({"type": "cancel"})})
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await bridge._client_input_loop()

    await sent(bridge)
    assert ws.types().count("turn_end") == 1


async def test_send_that_overrides_a_turn_closes_it_with_one_turn_end_each(make_bridge):
    bridge, ws = make_bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)
    bridge.orchestrator.stream_response = tokens("new")
    ws.inbox.put_nowait({"type": "websocket.message", "text": json.dumps({"type": "send", "text": "y"})})
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await bridge._client_input_loop()
    await asyncio.wait_for(bridge._active_turn, 2)

    await sent(bridge)
    ends = ws.of("turn_end")
    assert [e["turn_id"] for e in ends] == ["t-1", "t-2"]
    # t-1 closes before t-2 opens
    order = [(m["type"], m.get("turn_id")) for m in ws.sent if isinstance(m, dict)
             if m["type"] in ("turn_start", "turn_end")]
    assert order == [("turn_start", "t-1"), ("turn_end", "t-1"), ("turn_start", "t-2"), ("turn_end", "t-2")]


async def test_final_transcription_cancelling_a_turn_sends_turn_end(make_bridge):
    bridge, ws = make_bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await bridge._on_transcription("hola que tal", True)

    await sent(bridge)
    assert ws.types().count("turn_end") == 1


async def test_barge_in_sends_turn_end_then_interrupted_with_turn_id(make_bridge):
    bridge, ws = make_bridge()
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    bridge._active_turn = asyncio.create_task(bridge._call_orchestrator("x"))
    await asyncio.wait_for(started.wait(), 2)

    await bridge._on_transcription("hola que tal", False)

    await sent(bridge)
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


async def test_turn_seq_wraps_in_audio_header_instead_of_overflowing(make_bridge):
    bridge, ws = make_bridge(output_mode=("text", "audio"), tts=_tts_with_one_chunk())
    bridge.orchestrator.stream_response = tokens("a")
    bridge._turn_seq = 65535

    await bridge._call_orchestrator("x")

    await sent(bridge)
    frames = [m for m in ws.sent if isinstance(m, bytes)]
    assert frames == [b"\xa1\x00\x00\x01\x02"]
    start = ws.of("turn_start")[0]
    assert start["turn_id"] == "t-65536" and start["turn_seq"] == 0  # same value as the header


async def test_push_turn_seq_wraps_in_audio_header(make_bridge):
    bridge, ws = make_bridge(output_mode=("text", "audio"), tts=_tts_with_one_chunk())
    bridge._turn_seq = 65535

    await bridge.on_push_turn_start("sk")
    await wait_until(lambda: any(isinstance(m, bytes) for m in ws.sent))

    await sent(bridge)
    frames = [m for m in ws.sent if isinstance(m, bytes)]
    assert frames == [b"\xa1\x00\x00\x01\x02"]
    assert ws.of("turn_start")[0]["turn_seq"] == 0
    await bridge.close_all()


# ── G-18 ─────────────────────────────────────────────────────────────────────


async def test_disconnected_client_cancels_its_turn_instead_of_draining(make_bridge, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 30.0)
    bridge, ws = make_bridge()
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


async def test_client_gone_during_a_turn_cancels_the_orchestrator_stream(make_bridge):
    failing_ws = AsyncMock()
    failing_ws.send_json = AsyncMock(side_effect=RuntimeError("socket closed"))
    failing_ws.send_bytes = AsyncMock(side_effect=RuntimeError("socket closed"))
    sender = QueuedSender(failing_ws)
    bridge, _ = make_bridge(sender=sender)
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


async def test_close_all_starting_while_a_send_waits_for_the_cancel_leaves_no_turn(make_bridge, monkeypatch):
    """G-09 (review): `_start_turn` must re-check `_closing` after awaiting the
    cancellation of the previous turn, inside the lock."""
    monkeypatch.setattr(settings, "SESSION_TEARDOWN_S", 5.0)
    bridge, _ = make_bridge()
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
