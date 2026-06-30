import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import TurnRegistry, ClientRegistry


def make_dispatcher():
    turn_reg = TurnRegistry()
    client_reg = ClientRegistry()
    dispatcher = FrameDispatcher(turn_reg, client_reg)
    return dispatcher, turn_reg, client_reg


@pytest.mark.asyncio
async def test_res_started_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {"type": "res", "id": "req-1", "ok": True, "payload": {"status": "started", "runId": "r1"}}
    await dispatcher.dispatch(frame)
    assert q.empty()


@pytest.mark.asyncio
async def test_res_done_routes_to_turn_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {"type": "res", "id": "req-1", "ok": True, "payload": {"status": "done"}}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "done"
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_res_unknown_req_id_is_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    frame = {"type": "res", "id": "unknown-id", "ok": True, "payload": {}}
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_chat_event_routes_to_session_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 1, "state": "delta",
        "deltaText": "Hola",
    }
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "chat"
    assert data["deltaText"] == "Hola"


@pytest.mark.asyncio
async def test_chat_event_no_active_turn_routes_to_client_registry():
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 1, "deltaText": "Push!",
    }
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.deliver_push.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_chat_event_no_session_key_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    frame = {"type": "event", "event": "chat", "payload": {"deltaText": "no key"}}
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_agent_lifecycle_start_calls_on_push_turn_start():
    dispatcher, _, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 1,
        "data": {"phase": "start", "startedAt": 123},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.on_push_turn_start.assert_awaited_once_with("agent:main:client-a")


@pytest.mark.asyncio
async def test_agent_lifecycle_end_calls_on_push_turn_end():
    dispatcher, _, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 2,
        "data": {"phase": "end", "stopReason": "stop"},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.on_push_turn_end.assert_awaited_once_with("agent:main:client-a")


@pytest.mark.asyncio
async def test_health_and_tick_ignored():
    dispatcher, _, _ = make_dispatcher()
    for event_name in ("health", "tick", "presence", "sessions.changed"):
        frame = {"type": "event", "event": event_name, "payload": {}}
        await dispatcher.dispatch(frame)  # must not raise
