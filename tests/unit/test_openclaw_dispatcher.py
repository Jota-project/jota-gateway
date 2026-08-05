from unittest.mock import AsyncMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.reconnection import ConnectionState, ServiceStatus


def make_dispatcher():
    turn_reg = TurnRegistry()
    client_reg = ClientRegistry()
    dispatcher = FrameDispatcher(turn_reg, client_reg)
    return dispatcher, turn_reg, client_reg


def _connected_status() -> ServiceStatus:
    return ServiceStatus(
        name="tts",
        state=ConnectionState.CONNECTED,
        connected_at=None,
        reconnect_attempts=0,
        last_error=None,
    )


def make_real_bridge(client_id="hab_sito", push_enabled=True, tool_calls_enabled=False):
    """A real JotaBridge (not AsyncMock) so dispatcher tests can exercise
    _push_allowed() gating — mirrors tests/unit/test_bridge_push.py's
    make_bridge(), kept local here to stay self-contained (issue #109)."""
    client = Client(id=client_id, client_key="key-123", is_active=True)
    config = ClientConfig(push_enabled=push_enabled, tool_calls_enabled=tool_calls_enabled)
    ws = AsyncMock()
    orchestrator = AsyncMock()
    orchestrator.ping = AsyncMock(return_value=True)
    tracker = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=None)
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=orchestrator,
        tts=tts,
        tracker=tracker,
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    return bridge, ws


@pytest.mark.asyncio
async def test_res_started_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {
        "type": "res",
        "id": "req-1",
        "ok": True,
        "payload": {"status": "started", "runId": "r1"},
    }
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
        "runId": "r1",
        "seq": 1,
        "state": "delta",
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
        "runId": "r1",
        "seq": 1,
        "deltaText": "Push!",
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
        "runId": "r1",
        "seq": 1,
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
        "runId": "r1",
        "seq": 2,
        "data": {"phase": "end", "stopReason": "stop"},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.on_push_turn_end.assert_awaited_once_with("agent:main:client-a")


@pytest.mark.asyncio
async def test_agent_lifecycle_start_suppressed_when_normal_turn_active():
    """#112: an agent start event for a session_key that already has a
    normal turn queue registered must not open a push turn — it would
    duplicate the turn the client is already receiving via chat.send."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 1,
        "data": {"phase": "start", "startedAt": 123},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.on_push_turn_start.assert_not_awaited()
    assert q.empty()


@pytest.mark.asyncio
async def test_agent_lifecycle_end_always_forwarded_regardless_of_normal_turn():
    """#112 final-review correction: unlike "start", "end" is never gated on
    TurnRegistry state — on_push_turn_end() already no-ops safely when no
    push turn is open (issue #84), so the dispatcher forwards every "end"
    unconditionally and lets the bridge decide."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    turn_reg.register("req-1", "agent:main:client-a")
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 2,
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


@pytest.mark.asyncio
async def test_session_tool_start_routes_to_turn_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "data": {
            "phase": "start",
            "name": "exec",
            "toolCallId": "call-1",
            "args": {"command": "ls"},
        },
    }
    frame = {"type": "event", "event": "session.tool", "payload": payload}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "tool"
    assert data["phase"] == "start"
    assert data["name"] == "exec"


@pytest.mark.asyncio
async def test_session_tool_result_routes_to_turn_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    payload = {
        "sessionKey": "agent:main:client-a",
        "data": {
            "phase": "result",
            "name": "exec",
            "toolCallId": "call-1",
            "isError": False,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        },
    }
    frame = {"type": "event", "event": "session.tool", "payload": payload}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "tool"
    assert data["phase"] == "result"


@pytest.mark.asyncio
async def test_session_tool_update_phase_is_dropped():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    payload = {
        "sessionKey": "agent:main:client-a",
        "data": {"phase": "update", "name": "exec", "toolCallId": "call-1", "partialResult": {}},
    }
    frame = {"type": "event", "event": "session.tool", "payload": payload}
    await dispatcher.dispatch(frame)
    assert q.empty()


@pytest.mark.asyncio
async def test_session_tool_no_active_turn_routes_to_client_registry():
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "data": {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {}},
    }
    frame = {"type": "event", "event": "session.tool", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.deliver_push_tool_call.assert_awaited_once_with(payload["data"])


@pytest.mark.asyncio
async def test_session_tool_no_session_key_ignored():
    dispatcher, _, _ = make_dispatcher()
    frame = {
        "type": "event",
        "event": "session.tool",
        "payload": {"data": {"phase": "start", "name": "exec", "toolCallId": "call-1"}},
    }
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_dispatcher_chat_event_push_disabled_client_receives_nothing():
    """#109: a chat push event routed through the real dispatcher→bridge
    wiring for a push_enabled=False client, with NO turn_start ever sent,
    must not reach the client — the acceptance criterion verbatim."""
    dispatcher, _, client_reg = make_dispatcher()
    bridge, ws = make_real_bridge(push_enabled=False)
    client_reg.register("hab_sito", bridge)

    payload = {"sessionKey": "agent:main:hab_sito", "runId": "r1", "seq": 1, "deltaText": "Push!"}
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)

    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_session_tool_push_disabled_client_receives_nothing():
    dispatcher, _, client_reg = make_dispatcher()
    bridge, ws = make_real_bridge(push_enabled=False, tool_calls_enabled=True)
    client_reg.register("hab_sito", bridge)

    payload = {
        "sessionKey": "agent:main:hab_sito",
        "data": {
            "phase": "start",
            "name": "exec",
            "toolCallId": "call-1",
            "args": {"command": "ls"},
        },
    }
    frame = {"type": "event", "event": "session.tool", "payload": payload}
    await dispatcher.dispatch(frame)

    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_full_push_lifecycle_disabled_client_receives_nothing():
    """Full agent start → chat → tool → agent end sequence through the real
    dispatcher for a push_enabled=False client: zero frames at any point,
    not just the first one — on_push_turn_start's own gate (Task-1-independent,
    predates #109) already no-ops on start; this confirms deliver_push and
    deliver_push_tool_call independently hold the line for whatever arrives
    in between, and that nothing leaks around the (correctly) skipped
    turn_start/turn_end pair."""
    dispatcher, _, client_reg = make_dispatcher()
    bridge, ws = make_real_bridge(push_enabled=False, tool_calls_enabled=True)
    client_reg.register("hab_sito", bridge)

    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "start"}},
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "chat",
            "payload": {"sessionKey": "agent:main:hab_sito", "deltaText": "Push!"},
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "session.tool",
            "payload": {
                "sessionKey": "agent:main:hab_sito",
                "data": {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {}},
            },
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "end"}},
        }
    )

    ws.send_json.assert_not_awaited()
    assert bridge._push_turn_open is False


@pytest.mark.asyncio
async def test_dispatcher_agent_lifecycle_suppressed_full_sequence_with_active_turn():
    """#112: agent.start + chat + session.tool + agent.end interleaved with
    an active normal turn must never open/close a push turn. The normal
    turn's own turn_start/turn_end is sent by _call_orchestrator (bridge.py),
    outside the dispatcher's responsibility — this test only proves the
    dispatcher contributes zero extra client-facing frames and that all
    content still reaches the normal turn's queue."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge, ws = make_real_bridge(push_enabled=True, tool_calls_enabled=True)
    client_reg.register("hab_sito", bridge)
    q = turn_reg.register("req-1", "agent:main:hab_sito")

    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "start"}},
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "chat",
            "payload": {"sessionKey": "agent:main:hab_sito", "deltaText": "Hola"},
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "session.tool",
            "payload": {
                "sessionKey": "agent:main:hab_sito",
                "data": {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {}},
            },
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "session.tool",
            "payload": {
                "sessionKey": "agent:main:hab_sito",
                "data": {
                    "phase": "result",
                    "name": "exec",
                    "toolCallId": "call-1",
                    "isError": False,
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                },
            },
        }
    )
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "end"}},
        }
    )

    kind, data = q.get_nowait()
    assert kind == "chat"
    assert data["deltaText"] == "Hola"
    kind, data = q.get_nowait()
    assert kind == "tool"
    assert data["phase"] == "start"
    kind, data = q.get_nowait()
    assert kind == "tool"
    assert data["phase"] == "result"
    assert q.empty()

    assert bridge._push_turn_open is False
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_lifecycle_end_closes_push_turn_opened_before_normal_turn():
    """#112 final-review regression test: a push turn that opened *before* a
    normal turn registers for the same session_key must still be closable by
    its own agent end — suppressing "end" symmetrically with "start" (the
    original implementation) would orphan it: dangling turn_end, leaked TTS,
    push path dead for the rest of the session."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge, ws = make_real_bridge(push_enabled=True)
    client_reg.register("hab_sito", bridge)

    # Genuine push starts first — no normal turn registered yet.
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "start"}},
        }
    )
    assert bridge._push_turn_open is True
    push_turn_id = bridge._push_turn_id

    # A normal turn now registers for the same session_key while the push
    # turn is still open (e.g. the user replies mid-push).
    turn_reg.register("req-1", "agent:main:hab_sito")

    # The push run's own "end" must still close it, not be suppressed.
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "end"}},
        }
    )

    assert bridge._push_turn_open is False
    ws.send_json.assert_any_call({"type": "turn_end", "turn_id": push_turn_id})
