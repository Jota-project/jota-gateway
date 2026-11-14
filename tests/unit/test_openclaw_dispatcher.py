import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fogar_gateway.core.config import settings
from fogar_gateway.models.schemas import ClientConfig
from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from fogar_gateway.services.reconnection import ConnectionState, ServiceStatus
from tests.fakes.bridge import push, sent
from tests.fakes.openclaw import SendOnlyOpenClawWS


def push_open(bridge) -> bool:
    return bridge.turns.snapshot(0.0)["turn"]["kind"] == "push"


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


@pytest.fixture
def make_real_bridge(make_bridge):
    """A real FogarBridge (not AsyncMock) so dispatcher tests can exercise the push
    gating (issue #109)."""

    def _make(push_enabled=True, tool_calls_enabled=False):
        config = ClientConfig(push_enabled=push_enabled, tool_calls_enabled=tool_calls_enabled)
        return make_bridge(client_id="hab_sito", config=config)

    return _make


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
async def test_res_legacy_status_done_is_not_a_turn_end():
    """Pre-2026.6.11 servers ended a turn with `res status:"done"`; no longer supported:
    only the chat event with state "final" (or an ok:false res) ends a turn."""
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {"type": "res", "id": "req-1", "ok": True, "payload": {"status": "done"}}
    await dispatcher.dispatch(frame)
    assert q.empty()


@pytest.mark.asyncio
async def test_res_ok_false_routes_to_turn_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {"type": "res", "id": "req-1", "ok": False, "error": {"code": "x"}}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "done"
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_res_unknown_req_id_is_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    frame = {"type": "res", "id": "unknown-id", "ok": True, "payload": {}}
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_chat_event_routes_to_session_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    await dispatcher.dispatch(_ack("req-1", "r1"))
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
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 1,
        "deltaText": "Push!",
    }
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_called_once_with("chat", payload, run_id="r1")


@pytest.mark.asyncio
async def test_chat_event_no_session_key_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    frame = {"type": "event", "event": "chat", "payload": {"deltaText": "no key"}}
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_agent_lifecycle_start_calls_on_push_turn_start():
    dispatcher, _, client_reg = make_dispatcher()
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 1,
        "data": {"phase": "start", "startedAt": 123},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_called_once_with("turn_start", "agent:main:client-a", run_id="r1")


@pytest.mark.asyncio
async def test_agent_lifecycle_end_calls_on_push_turn_end():
    dispatcher, _, client_reg = make_dispatcher()
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 2,
        "data": {"phase": "end", "stopReason": "stop"},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_called_once_with("turn_end", "agent:main:client-a", run_id="r1")


@pytest.mark.asyncio
async def test_agent_lifecycle_start_suppressed_when_normal_turn_active():
    """#112: an agent start event for a session_key that already has a
    normal turn queue registered must not open a push turn — it would
    duplicate the turn the client is already receiving via chat.send."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 1,
        "data": {"phase": "start", "startedAt": 123},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_not_called()
    assert q.empty()


@pytest.mark.asyncio
async def test_agent_lifecycle_end_always_forwarded_regardless_of_normal_turn():
    """#112 final-review correction: unlike "start", "end" is never gated on
    TurnRegistry state — on_push_turn_end() already no-ops safely when no
    push turn is open (issue #84), so the dispatcher forwards every "end"
    unconditionally and lets the bridge decide."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    turn_reg.register("req-1", "agent:main:client-a")
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1",
        "seq": 2,
        "data": {"phase": "end", "stopReason": "stop"},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_called_once_with("turn_end", "agent:main:client-a", run_id="r1")


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
    await dispatcher.dispatch(_ack("req-1", "r1"))
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
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "data": {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {}},
    }
    frame = {"type": "event", "event": "session.tool", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_called_once_with("tool", payload["data"], run_id=None)


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
async def test_dispatcher_chat_event_push_disabled_client_receives_nothing(make_real_bridge):
    """#109: a chat push event routed through the real dispatcher→bridge
    wiring for a push_enabled=False client, with NO turn_start ever sent,
    must not reach the client — the acceptance criterion verbatim."""
    dispatcher, _, client_reg = make_dispatcher()
    bridge, _ = make_real_bridge(push_enabled=False)
    client_reg.register("hab_sito", bridge)

    payload = {"sessionKey": "agent:main:hab_sito", "runId": "r1", "seq": 1, "deltaText": "Push!"}
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)
    await bridge.push_idle()

    assert await sent(bridge) == []
    await bridge.close_all()


@pytest.mark.asyncio
async def test_dispatcher_session_tool_push_disabled_client_receives_nothing(make_real_bridge):
    dispatcher, _, client_reg = make_dispatcher()
    bridge, _ = make_real_bridge(push_enabled=False, tool_calls_enabled=True)
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
    await bridge.push_idle()

    assert await sent(bridge) == []
    await bridge.close_all()


@pytest.mark.asyncio
async def test_dispatcher_full_push_lifecycle_disabled_client_receives_nothing(make_real_bridge):
    """Full agent start → chat → tool → agent end sequence through the real
    dispatcher for a push_enabled=False client: zero frames at any point,
    not just the first one — on_push_turn_start's own gate (Task-1-independent,
    predates #109) already no-ops on start; this confirms deliver_push and
    deliver_push_tool_call independently hold the line for whatever arrives
    in between, and that nothing leaks around the (correctly) skipped
    turn_start/turn_end pair."""
    dispatcher, _, client_reg = make_dispatcher()
    bridge, _ = make_real_bridge(push_enabled=False, tool_calls_enabled=True)
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

    await bridge.push_idle()

    assert await sent(bridge) == []
    assert not push_open(bridge)
    await bridge.close_all()


@pytest.mark.asyncio
async def test_dispatcher_agent_lifecycle_suppressed_full_sequence_with_active_turn(make_real_bridge):
    """#112: agent.start + chat + session.tool + agent.end interleaved with
    an active normal turn must never open/close a push turn. The normal
    turn's own turn_start/turn_end is sent by _call_orchestrator (bridge.py),
    outside the dispatcher's responsibility — this test only proves the
    dispatcher contributes zero extra client-facing frames and that all
    content still reaches the normal turn's queue."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge, _ = make_real_bridge(push_enabled=True, tool_calls_enabled=True)
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

    await bridge.push_idle()
    assert not push_open(bridge)
    assert await sent(bridge) == []
    await bridge.close_all()


@pytest.mark.asyncio
async def test_agent_lifecycle_end_closes_push_turn_opened_before_normal_turn(make_real_bridge):
    """#112 final-review regression test: a push turn that opened *before* a
    normal turn registers for the same session_key must still be closable by
    its own agent end — suppressing "end" symmetrically with "start" (the
    original implementation) would orphan it: dangling turn_end, leaked TTS,
    push path dead for the rest of the session."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge, _ = make_real_bridge(push_enabled=True)
    client_reg.register("hab_sito", bridge)

    # Genuine push starts first — no normal turn registered yet.
    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "start"}},
        }
    )
    await bridge.push_idle()
    assert push_open(bridge)
    (push_turn_start,) = [m for m in await sent(bridge) if m["type"] == "turn_start"]
    push_turn_id = push_turn_start["turn_id"]

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

    await bridge.push_idle()
    assert not push_open(bridge)
    assert {"type": "turn_end", "turn_id": push_turn_id} in await sent(bridge)
    await bridge.close_all()


@pytest.mark.asyncio
async def test_dispatch_agent_end_does_not_wait_for_slow_tts_drain(make_bridge, monkeypatch):
    """#130 S3a — el motivo de todo el cambio: un push cuyo audio TTS tarda en
    drenar no debe retener a dispatch() (y por tanto a _listen y al resto de
    sesiones multiplexadas)."""
    # Pinned so the proof does not depend on the default drain timeout.
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 30)
    dispatcher, _, client_reg = make_dispatcher()
    audio_cancelled = asyncio.Event()

    async def stuck_audio():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            audio_cancelled.set()
            raise
        yield b""  # pragma: no cover

    stream = AsyncMock()
    stream.get_audio_stream = stuck_audio
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=stream)
    bridge, _ = make_bridge(client_id="hab_sito", output_mode=("audio", "text"), tts=tts)
    client_reg.register("hab_sito", bridge)
    await push(bridge, ("turn_start", "agent:main:hab_sito"))

    await asyncio.wait_for(
        dispatcher.dispatch(
            {
                "type": "event",
                "event": "agent",
                "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "end"}},
            }
        ),
        timeout=0.5,
    )
    # What is proven: dispatch() returned within the 0.5s wait_for although the
    # drain it enqueued would take PUSH_TTS_DRAIN_TIMEOUT_S (30s) — dispatch did
    # not wait for the handler. (pending == 1 only shows the event is queued.)
    assert bridge.turns.snapshot(0.0)["push_backlog"]["pending"] == 1

    # Cleanup: close_all() must not hang on the stuck audio task.
    await asyncio.wait_for(bridge.close_all(), timeout=2.0)
    assert audio_cancelled.is_set()


@pytest.mark.asyncio
async def test_dispatch_keeps_the_112_decision_synchronous_in_listen(make_real_bridge):
    """The normal-turn check happens when the frame arrives (in _listen), not when
    the worker later runs: a turn registered *after* dispatch must not retroactively
    suppress an already-enqueued start."""
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge, _ = make_real_bridge(push_enabled=True)
    client_reg.register("hab_sito", bridge)

    await dispatcher.dispatch(
        {
            "type": "event",
            "event": "agent",
            "payload": {"sessionKey": "agent:main:hab_sito", "data": {"phase": "start"}},
        }
    )
    turn_reg.register("req-1", "agent:main:hab_sito")  # registered after the frame
    await bridge.push_idle()

    assert push_open(bridge)
    await bridge.close_all()


# --- Issue #136: unknown frames/events must not vanish silently -------------


async def test_unknown_event_type_is_logged_once_at_info(caplog):
    import logging

    dispatcher, _, _ = make_dispatcher()
    frame = {"type": "event", "event": "brand.new.event", "payload": {"secret": "x"}}

    with caplog.at_level(logging.DEBUG, logger="fogar_gateway.services.openclaw.dispatcher"):
        await dispatcher.dispatch(frame)
        await dispatcher.dispatch(frame)

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "brand.new.event" in infos[0].getMessage()
    # payload content must never reach the logs
    assert "secret" not in caplog.text


async def test_unknown_frame_type_is_logged_once_at_info(caplog):
    import logging

    dispatcher, _, _ = make_dispatcher()

    with caplog.at_level(logging.DEBUG, logger="fogar_gateway.services.openclaw.dispatcher"):
        await dispatcher.dispatch({"type": "weird"})
        await dispatcher.dispatch({"type": "weird"})

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "weird" in infos[0].getMessage()


async def test_known_events_are_not_reported_as_unknown(caplog):
    import logging

    dispatcher, _, _ = make_dispatcher()

    with caplog.at_level(logging.DEBUG, logger="fogar_gateway.services.openclaw.dispatcher"):
        await dispatcher.dispatch({"type": "event", "event": "chat", "payload": {}})
        await dispatcher.dispatch({"type": "event", "event": "agent", "payload": {}})
        await dispatcher.dispatch({"type": "event", "event": "session.tool", "payload": {}})
        await dispatcher.dispatch({"type": "res", "id": "x", "payload": {}})

    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


# --- #285: frames of a run the gateway aborted are dropped by runId ----------

SK = "agent:main:client-a"


def _ack(req_id, run_id):
    return {"type": "res", "id": req_id, "ok": True, "payload": {"runId": run_id, "status": "started"}}


def _chat(run_id, state="delta", text="late"):
    payload = {"sessionKey": SK, "runId": run_id, "state": state, "deltaText": text}
    return {"type": "event", "event": "chat", "payload": payload}


def _agent(run_id, phase):
    payload = {"sessionKey": SK, "runId": run_id, "data": {"phase": phase}}
    return {"type": "event", "event": "agent", "payload": payload}


def _tool(run_id):
    data = {"phase": "start", "name": "exec", "toolCallId": "c1", "args": {}}
    return {"type": "event", "event": "session.tool", "payload": {"sessionKey": SK, "runId": run_id, "data": data}}


async def test_frames_of_an_abandoned_turn_never_reach_the_next_turn_nor_a_push():
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    turn_reg.register("req-1", SK)
    await dispatcher.dispatch(_ack("req-1", "run-1"))
    turn_reg.abandon("req-1")  # barge-in: the turn is aborted
    turn_reg.unregister(SK, "req-1")
    q = turn_reg.register("req-2", SK)  # the next turn
    await dispatcher.dispatch(_ack("req-2", "run-2"))

    for frame in (_agent("run-1", "start"), _chat("run-1"), _tool("run-1"), _chat("run-1", "final"),
                  _agent("run-1", "end")):  # fmt: skip
        await dispatcher.dispatch(frame)
    assert q.empty()
    turn_reg.unregister(SK, "req-2")
    for frame in (_agent("run-1", "start"), _chat("run-1"), _agent("run-1", "end")):
        await dispatcher.dispatch(frame)
    bridge.enqueue_push.assert_not_called()

    await dispatcher.dispatch(_agent("run-3", "start"))  # another run still gets through
    bridge.enqueue_push.assert_called_once_with("turn_start", SK, run_id="run-3")


async def test_a_turn_aborted_before_its_ack_gets_its_run_tombstoned_by_the_late_ack():
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    turn_reg.register("req-1", SK)
    turn_reg.abandon("req-1")
    turn_reg.unregister(SK, "req-1")
    await dispatcher.dispatch(_ack("req-1", "run-1"))  # the ack arrives after the abort
    await dispatcher.dispatch(_chat("run-1"))
    bridge.enqueue_push.assert_not_called()


async def test_chat_abort_answer_tombstones_the_runs_it_aborted():
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    res = {"type": "res", "id": "abort-1", "ok": True, "payload": {"ok": True, "aborted": True, "runIds": ["run-9"]}}
    await dispatcher.dispatch(res)
    await dispatcher.dispatch(_agent("run-9", "start"))
    bridge.enqueue_push.assert_not_called()
    assert turn_reg.is_run_aborted("run-9")
    assert not turn_reg.is_run_aborted("run-8")
    assert not turn_reg.is_run_aborted(None)


async def test_run_tombstone_expires(monkeypatch):
    import fogar_gateway.services.openclaw.registry as registry

    turn_reg = TurnRegistry()
    now = [1000.0]
    monkeypatch.setattr(registry.time, "monotonic", lambda: now[0])
    turn_reg.abort_run("run-1")
    now[0] += registry.RUN_ABORT_TOMBSTONE_S - 1
    assert turn_reg.is_run_aborted("run-1")
    now[0] += 2
    assert not turn_reg.is_run_aborted("run-1")


async def test_agent_error_phase_is_forwarded_as_turn_error():
    """#300: a failed push run ends with `error`; the bridge closes it with reason error."""
    dispatcher, _, client_reg = make_dispatcher()
    bridge = MagicMock()
    client_reg.register("client-a", bridge)
    await dispatcher.dispatch(_agent("run-1", "error"))
    bridge.enqueue_push.assert_called_once_with("turn_error", SK, run_id="run-1")


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_frames_of_another_run_never_reach_the_turn():
    """#285 (review 1): once the ack named the turn's run, every frame of another run on
    the same key is dropped — deltas and tool events too, not only its terminal."""
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", SK)
    await dispatcher.dispatch(_ack("req-1", "run-1"))
    for frame in (_chat("run-push"), _tool("run-push"), _chat("run-push", "final"), _chat("run-1", text="mio")):
        await dispatcher.dispatch(frame)
    assert [(k, d.get("deltaText")) for k, d in _drain(q)] == [("chat", "mio")]


async def test_frames_before_the_ack_are_held_until_it_names_the_run():
    """#285 (review 2): frames with a run id that arrive before the turn's ack are held;
    the ack delivers those of its run, in order, and drops the rest."""
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", SK)
    for frame in (_chat("run-old", text="viejo"), _chat("run-1", text="a"), _chat("run-old", "final"),
                  _chat("run-1", text="b")):  # fmt: skip
        await dispatcher.dispatch(frame)
    assert q.empty()
    await dispatcher.dispatch(_ack("req-1", "run-1"))
    assert [d["deltaText"] for _, d in _drain(q)] == ["a", "b"]


async def test_an_ack_without_run_id_delivers_what_was_held():
    """A server whose ack names no run (older than 2026.10.5) loses nothing."""
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", SK)
    await dispatcher.dispatch(_chat("run-x", text="a"))
    await dispatcher.dispatch({"type": "res", "id": "req-1", "ok": True, "payload": {"status": "started"}})
    await dispatcher.dispatch(_chat("run-x", text="b"))
    assert [d["deltaText"] for _, d in _drain(q)] == ["a", "b"]


async def test_held_frames_are_bounded_like_the_queue():
    turn_reg = TurnRegistry(max_queue_size=2)
    dispatcher = FrameDispatcher(turn_reg, ClientRegistry())
    q = turn_reg.register("req-1", SK)
    for i in range(3):
        await dispatcher.dispatch(_chat("run-1", text=str(i)))
    assert _drain(q) == [("overflow", None)]
    await dispatcher.dispatch(_ack("req-1", "run-1"))
    assert q.empty()


async def test_abort_registration_takes_every_frame_of_the_key():
    """chat.abort's own registration is never acked through the dispatcher: it filters
    nothing by run (it waits for the `aborted` of a run it does not know)."""
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("abort-1", SK, by_run=False)
    await dispatcher.dispatch(_chat("run-push", "aborted"))
    assert [d["state"] for _, d in _drain(q)] == ["aborted"]


async def test_late_terminal_of_an_aborted_turn_before_both_acks_does_not_close_the_next():
    """#285 (review 2), the exact ordering: turn A is aborted before its ack; turn B is
    sent; A's late reply (delta + final) arrives before B's ack, then B's ack, then A's
    late ack, then B's own reply. B gets only its own text and ends on its own final."""
    from fogar_gateway.services.openclaw.client import OpenClawClient

    turns = TurnRegistry()
    dispatcher = FrameDispatcher(turns, ClientRegistry())
    client = OpenClawClient("127.0.0.1", 1, "tok", turns, dispatcher)
    client._ws = ws = SendOnlyOpenClawWS()  # no connect: the test is the server

    async def collect(text):
        return [e async for e in client.stream_response(text, session_key=SK)]

    first = asyncio.create_task(collect("uno"))
    while len(ws.ids("chat.send")) < 1:
        await asyncio.sleep(0)
    first.cancel()  # barge-in before A's ack
    await asyncio.gather(first, return_exceptions=True)
    second = asyncio.create_task(collect("dos"))
    while len(ws.ids("chat.send")) < 2:
        await asyncio.sleep(0)
    req_a, req_b = ws.ids("chat.send")

    await dispatcher.dispatch(_chat("run-a", text="viejo"))
    await dispatcher.dispatch(_chat("run-a", "final"))
    await dispatcher.dispatch(_ack(req_b, "run-b"))
    await dispatcher.dispatch(_ack(req_a, "run-a"))
    await dispatcher.dispatch(_chat("run-a", text="aun mas viejo"))
    await dispatcher.dispatch(_chat("run-b", text="nuevo"))
    await dispatcher.dispatch(_chat("run-b", "final", text=""))
    events = await asyncio.wait_for(second, 2)

    assert "".join(e.content for e in events if e.type == "token") == "nuevo"
    assert (events[-1].type, events[-1].content) == ("status", "done")
    assert turns.is_run_aborted("run-a")


async def test_frames_without_run_id_wait_behind_held_ones():
    """Review 6: once a frame is held before the ack, the ones without run id queue behind
    it, so the turn sees them in arrival order; the held ones count in the depth."""
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", SK)
    plain = {"sessionKey": SK, "state": "delta", "deltaText": "b"}
    await dispatcher.dispatch(_chat("run-1", text="a"))
    await dispatcher.dispatch({"type": "event", "event": "chat", "payload": plain})
    assert q.empty() and turn_reg.depth(SK) == (2, 2)
    await dispatcher.dispatch(_ack("req-1", "run-1"))
    assert [d["deltaText"] for _, d in _drain(q)] == ["a", "b"]


async def test_held_frames_overflow_once(caplog):
    """Review 7: after the overflow the turn takes nothing more (one warning, one item)."""
    turn_reg = TurnRegistry(max_queue_size=2)
    dispatcher = FrameDispatcher(turn_reg, ClientRegistry())
    q = turn_reg.register("req-1", SK)
    for i in range(6):
        await dispatcher.dispatch(_chat("run-1", text=str(i)))
    assert _drain(q) == [("overflow", None)]
    assert caplog.text.count("turn queue overflow") == 1


async def test_a_rejected_send_is_delivered_even_with_frames_held():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", SK)
    await dispatcher.dispatch(_chat("run-other", text="x"))
    await dispatcher.dispatch({"type": "res", "id": "req-1", "ok": False, "error": {"code": "X"}})
    assert [k for k, _ in _drain(q)] == ["done"]


async def test_active_runs_follow_their_frames():
    """Review 3: a run is active on its key from its first frame to its terminal one; a
    tombstone or a lost connection forgets it."""
    dispatcher, turn_reg, _ = make_dispatcher()
    await dispatcher.dispatch(_agent("run-1", "start"))
    await dispatcher.dispatch(_chat("run-2"))
    assert sorted(turn_reg.active_runs(SK)) == ["run-1", "run-2"]
    assert turn_reg.active_runs("agent:otro:client-a") == []
    await dispatcher.dispatch(_chat("run-2", "final"))
    assert turn_reg.active_runs(SK) == ["run-1"]
    await dispatcher.dispatch(_agent("run-1", "end"))
    assert turn_reg.active_runs(SK) == []
    await dispatcher.dispatch(_agent("run-3", "start"))
    turn_reg.abort_run("run-3")
    assert turn_reg.active_runs(SK) == []
    await dispatcher.dispatch(_tool("run-4"))
    turn_reg.error_all("orchestrator_disconnected")
    assert turn_reg.active_runs(SK) == []
