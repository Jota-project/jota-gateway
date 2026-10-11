"""#130 S3b: cola de turno acotada en TurnRegistry + dispatcher sin bloqueo."""

import asyncio
from unittest.mock import MagicMock

import pytest

import src.services.openclaw.registry as registry_mod
from src.core.config import settings
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import ClientRegistry, TurnRegistry

SK = "agent:main:client-a"


def test_register_creates_bounded_queue_from_setting(monkeypatch):
    monkeypatch.setattr(settings, "TURN_QUEUE_MAX", 7)
    q = TurnRegistry().register("req-1", SK)
    assert q.maxsize == 7


def test_deliver_returns_false_without_turn():
    reg = TurnRegistry()
    assert reg.deliver_by_session(SK, ("chat", {})) is False
    assert reg.deliver_by_req("nope", ("done", {})) is False


def test_deliver_puts_item_without_blocking():
    reg = TurnRegistry(max_queue_size=2)
    q = reg.register("req-1", SK)
    assert reg.deliver_by_session(SK, ("chat", {"n": 1})) is True
    assert reg.deliver_by_req("req-1", ("done", {})) is True
    assert q.qsize() == 2


def test_overflow_drains_queue_and_leaves_single_marker():
    reg = TurnRegistry(max_queue_size=2)
    q = reg.register("req-1", SK)
    for i in range(3):
        assert reg.deliver_by_session(SK, ("chat", {"n": i})) is True
    assert q.qsize() == 1
    assert q.get_nowait() == ("overflow", None)
    assert reg.is_aborted(SK) is True


def test_frames_after_overflow_are_dropped_not_queued():
    reg = TurnRegistry(max_queue_size=1)
    q = reg.register("req-1", SK)
    reg.deliver_by_session(SK, ("chat", {}))
    reg.deliver_by_session(SK, ("chat", {}))  # overflow
    assert reg.deliver_by_session(SK, ("chat", {})) is True  # owned, but dropped
    assert reg.deliver_by_req("req-1", ("done", {})) is True
    assert q.qsize() == 1


def test_unregister_keeps_tombstone():
    reg = TurnRegistry(max_queue_size=1)
    reg.register("req-1", SK)
    reg.deliver_by_session(SK, ("chat", {}))
    reg.deliver_by_session(SK, ("chat", {}))
    reg.unregister(SK, "req-1")
    assert reg.get_queue_by_session(SK) is None
    assert reg.deliver_by_session(SK, ("chat", {})) is False
    assert reg.is_aborted(SK) is True


def test_tombstone_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(registry_mod.time, "monotonic", lambda: now[0])
    reg = TurnRegistry(max_queue_size=1)
    reg.register("req-1", SK)
    reg.deliver_by_session(SK, ("chat", {}))
    reg.deliver_by_session(SK, ("chat", {}))
    reg.unregister(SK, "req-1")
    assert reg.is_aborted(SK) is True
    now[0] += registry_mod.TURN_ABORT_TOMBSTONE_S + 0.1
    assert reg.is_aborted(SK) is False


def test_new_turn_after_overflow_starts_clean():
    reg = TurnRegistry(max_queue_size=1)
    reg.register("req-a", SK)
    reg.deliver_by_session(SK, ("chat", {}))
    reg.deliver_by_session(SK, ("chat", {}))
    reg.unregister(SK, "req-a")
    q = reg.register("req-b", SK)
    reg.unregister(SK, "req-a")  # stale unregister must not touch req-b
    assert reg.deliver_by_session(SK, ("chat", {"n": 1})) is True
    assert q.get_nowait() == ("chat", {"n": 1})


def test_error_all_does_not_raise_on_full_queue():
    reg = TurnRegistry(max_queue_size=1)
    q = reg.register("req-1", SK)
    reg.deliver_by_session(SK, ("chat", {}))
    reg.error_all("connection closed")
    assert q.get_nowait() == ("error", "connection closed")


def _chat_frame(text="x"):
    return {
        "type": "event",
        "event": "chat",
        "payload": {"sessionKey": SK, "state": "delta", "deltaText": text},
    }


def _make():
    turns = TurnRegistry(max_queue_size=2)
    clients = ClientRegistry()
    bridge = MagicMock()
    clients.register("client-a", bridge)
    return FrameDispatcher(turns, clients), turns, bridge


@pytest.mark.asyncio
async def test_overflow_never_blocks_dispatch_and_is_not_pushed():
    dispatcher, turns, bridge = _make()
    q = turns.register("req-1", SK)

    async def flood():
        for i in range(10):
            await dispatcher.dispatch(_chat_frame(str(i)))

    await asyncio.wait_for(flood(), timeout=1.0)  # a blocking put would hang here
    assert q.get_nowait() == ("overflow", None)
    assert q.empty()
    bridge.enqueue_push.assert_not_called()


@pytest.mark.asyncio
async def test_late_frames_of_aborted_turn_are_not_rerouted_as_push():
    dispatcher, turns, bridge = _make()
    turns.register("req-1", SK)
    for _ in range(3):
        await dispatcher.dispatch(_chat_frame())  # third one overflows
    turns.unregister(SK, "req-1")  # stream_response's finally

    await dispatcher.dispatch(_chat_frame())
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "session.tool",
            "payload": {"sessionKey": SK, "data": {"phase": "start", "name": "t"}},
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": SK, "data": {"phase": "start"}},
        }
    )
    bridge.enqueue_push.assert_not_called()

    # agent end is still always forwarded (#112/#84)
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": SK, "data": {"phase": "end"}},
        }
    )
    bridge.enqueue_push.assert_called_once_with("turn_end", SK)
