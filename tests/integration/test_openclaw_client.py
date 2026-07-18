import asyncio
import json
import uuid
from typing import Optional
from unittest.mock import patch

import pytest

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import TurnRegistry, ClientRegistry, TURN_IN_PROGRESS_ERROR
from src.services.reconnection import ConnectionState

HELLO_OK_PAYLOAD = {
    "type": "hello-ok", "protocol": 4,
    "server": {"version": "2026.6.6", "connId": "test-conn"},
    "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400, "maxBufferedBytes": 0},
    "snapshot": {
        "defaultAgentId": "main",
        "agents": [
            {"agentId": "main", "name": "Main Agent", "isDefault": True, "heartbeat": {}},
            {"agentId": "assistant", "name": "Jota Voice", "isDefault": False, "heartbeat": {}},
        ],
        "sessionDefaults": {"defaultAgentId": "main"},
    },
    "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
}

# Matches OpenClaw server 2026.6.11+: hello-ok's snapshot no longer embeds the
# agent roster at all — it must be fetched separately via agents.list.
HELLO_OK_PAYLOAD_NO_SNAPSHOT_AGENTS = {
    "type": "hello-ok", "protocol": 4,
    "server": {"version": "2026.6.11", "connId": "test-conn-2"},
    "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400, "maxBufferedBytes": 0},
    "snapshot": {
        "presence": {},
        "health": {},
        "stateVersion": 1,
        "uptimeMs": 1000,
        "sessionDefaults": {"defaultAgentId": "main"},
    },
    "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
}

AGENTS_LIST_PAYLOAD = {
    "defaultId": "main",
    "mainKey": "main",
    "scope": "per-sender",
    "agents": [
        {"id": "main", "name": "Main Agent"},
        {"id": "assistant", "name": "Jota Voice"},
        {"id": "ci-tester", "name": "CI Tester"},
    ],
}


class SmartFakeWS:
    """Queue-backed fake WebSocket that auto-responds to OpenClaw protocol v4.

    chat_responses: {sessionKey → [list of deltaText strings]}
    """

    def __init__(
        self,
        chat_responses: Optional[dict] = None,
        chat_response_delay: float = 0.0,
        hello_ok_payload: Optional[dict] = None,
        agents_list_payload: Optional[dict] = None,
        tool_calls: Optional[dict] = None,
    ):
        self.chat_responses: dict[str, list[str]] = chat_responses or {}
        self.chat_response_delay: float = chat_response_delay  # delay between chunks (in seconds)
        self.hello_ok_payload = hello_ok_payload or HELLO_OK_PAYLOAD
        self.agents_list_payload = agents_list_payload or AGENTS_LIST_PAYLOAD
        self.tool_calls: dict[str, list[dict]] = tool_calls or {}
        self._to_client: asyncio.Queue = asyncio.Queue()
        self._from_client: asyncio.Queue = asyncio.Queue()
        self.sent_frames: list[dict] = []
        self.closed = False
        self._handler: Optional[asyncio.Task] = None

    async def start(self):
        self._handler = asyncio.create_task(self._auto_respond())

    async def recv(self) -> str:
        msg = await self._to_client.get()
        if msg is None:
            raise Exception("connection closed")
        return msg

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        self.sent_frames.append(frame)
        await self._from_client.put(data)

    async def close(self) -> None:
        self.closed = True
        await self._to_client.put(None)
        if self._handler:
            self._handler.cancel()

    def __await__(self):
        # Allows `await websockets.connect(...)` when patched with return_value=fake_ws
        async def _noop():
            return self
        return _noop().__await__()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        msg = await self._to_client.get()
        if msg is None:
            raise StopAsyncIteration
        return msg

    async def _auto_respond(self):
        # 1. Send challenge
        await self._to_client.put(json.dumps({
            "type": "event", "event": "connect.challenge",
            "payload": {"nonce": "test-nonce", "ts": 1234567890},
        }))

        while True:
            raw = await self._from_client.get()
            frame = json.loads(raw)
            method = frame.get("method")
            req_id = frame.get("id", "")
            params = frame.get("params", {})

            if method == "connect":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": self.hello_ok_payload,
                }))

            elif method == "agents.list":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": self.agents_list_payload,
                }))

            elif method == "sessions.subscribe":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"subscribed": True},
                }))

            elif method == "chat.send":
                sk = params.get("sessionKey", "")
                run_id = str(uuid.uuid4())
                chunks = self.chat_responses.get(sk, ["Hello"])

                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"runId": run_id, "status": "started"},
                }))
                if self.chat_response_delay > 0:
                    await asyncio.sleep(self.chat_response_delay)
                for tool_data in self.tool_calls.get(sk, []):
                    await self._to_client.put(json.dumps({
                        "type": "event", "event": "session.tool",
                        "payload": {"sessionKey": sk, "runId": run_id, "data": tool_data},
                    }))
                for i, chunk in enumerate(chunks):
                    is_last = i == len(chunks) - 1
                    await self._to_client.put(json.dumps({
                        "type": "event", "event": "chat",
                        "payload": {
                            "runId": run_id, "sessionKey": sk,
                            "seq": i + 1,
                            "state": "final" if is_last else "delta",
                            "deltaText": chunk,
                        },
                    }))
                    if self.chat_response_delay > 0:
                        await asyncio.sleep(self.chat_response_delay)

            elif method == "health":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"ok": True},
                }))

            elif method == "chat.abort":
                pass


def make_client(fake_ws: SmartFakeWS) -> OpenClawClient:
    turn_reg = TurnRegistry()
    client_reg = ClientRegistry()
    dispatcher = FrameDispatcher(turn_reg, client_reg)
    client = OpenClawClient("127.0.0.1", 18789, "test-token", turn_reg, dispatcher)
    return client


@pytest.fixture
def fake_ws():
    return SmartFakeWS({"agent:main:client-a": ["Hola ", "mundo"]})


async def connected_client(fake_ws: SmartFakeWS) -> OpenClawClient:
    client = make_client(fake_ws)
    await fake_ws.start()
    with patch("websockets.connect", return_value=fake_ws):
        await client.connect()
    return client


@pytest.mark.asyncio
async def test_connect_returns_gateway_info(fake_ws):
    client = await connected_client(fake_ws)
    assert client.gateway_info is not None
    assert client.gateway_info.default_agent_id == "main"
    assert client.gateway_info.has_agent("assistant")
    await client.close()


@pytest.mark.asyncio
async def test_connect_populates_agents_via_agents_list_when_snapshot_lacks_agents():
    """OpenClaw server 2026.6.11+ no longer embeds the agent roster in
    hello-ok's snapshot — it must be fetched via a separate agents.list call
    right after connecting, or has_agent() silently rejects every agent."""
    fake_ws = SmartFakeWS(
        {"agent:main:client-a": ["Hola"]},
        hello_ok_payload=HELLO_OK_PAYLOAD_NO_SNAPSHOT_AGENTS,
    )
    client = await connected_client(fake_ws)
    assert client.gateway_info.has_agent("main")
    assert client.gateway_info.has_agent("assistant")
    assert client.gateway_info.has_agent("ci-tester")
    assert client.gateway_info.default_agent_id == "main"
    await client.close()


@pytest.mark.asyncio
async def test_connect_sends_sessions_subscribe(fake_ws):
    client = await connected_client(fake_ws)
    methods = [f["method"] for f in fake_ws.sent_frames]
    assert "sessions.subscribe" in methods
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_tokens(fake_ws):
    client = await connected_client(fake_ws)
    tokens = []
    async for event in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        if event.type == "token":
            tokens.append(event.content)
    assert tokens == ["Hola ", "mundo"]
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_ends_with_status_done(fake_ws):
    client = await connected_client(fake_ws)
    events = []
    async for event in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        events.append(event)
    assert events[-1].type == "status"
    assert events[-1].content == "done"
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_uses_sessionKey_format(fake_ws):
    client = await connected_client(fake_ws)
    async for _ in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        pass
    chat_sends = [f for f in fake_ws.sent_frames if f.get("method") == "chat.send"]
    assert len(chat_sends) == 1
    assert "sessionKey" in chat_sends[0]["params"]
    assert "session" not in chat_sends[0]["params"]
    await client.close()


@pytest.mark.asyncio
async def test_ping_returns_true_when_connected(fake_ws):
    client = await connected_client(fake_ws)
    result = await client.ping()
    assert result is True
    await client.close()


@pytest.mark.asyncio
async def test_ping_returns_false_when_not_connected():
    client = make_client(SmartFakeWS())
    result = await client.ping()
    assert result is False


@pytest.mark.asyncio
async def test_session_key_required(fake_ws):
    client = await connected_client(fake_ws)
    with pytest.raises(ValueError, match="session_key is required"):
        async for _ in client.stream_response("hola", "client-a"):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_on_disconnect_called_on_ws_drop(fake_ws):
    client = await connected_client(fake_ws)
    disconnected = []
    client.on_disconnect = lambda: disconnected.append(True)
    await fake_ws.close()
    await asyncio.sleep(0.05)
    assert disconnected == [True]


@pytest.mark.asyncio
async def test_connect_frame_schema(fake_ws):
    """connect frame must carry protocol range, auth token, and operator role."""
    client = await connected_client(fake_ws)
    connect_frames = [f for f in fake_ws.sent_frames if f.get("method") == "connect"]
    assert len(connect_frames) == 1
    p = connect_frames[0]["params"]
    assert p["minProtocol"] == 3
    assert p["maxProtocol"] == 4
    assert p["role"] == "operator"
    assert "token" in p["auth"]
    assert p["client"]["mode"] == "backend"
    await client.close()


@pytest.mark.asyncio
async def test_chat_abort_frame_schema(fake_ws):
    """chat.abort sent on task cancellation must use flat sessionKey, not nested session.key."""
    # Create a fake WS with delays so cancellation races an in-flight response (not a completed one).
    slow_fake_ws = SmartFakeWS(
        {"agent:main:client-a": ["Hola ", "mundo"]},
        chat_response_delay=0.15  # 150ms delay between responses
    )
    client = await connected_client(slow_fake_ws)

    async def consume():
        async for _ in client.stream_response(
            "hola", "client-a", session_key="agent:main:client-a"
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)  # let first chunk queue before cancel
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.05)  # let shield-wrapped abort send complete

    aborts = [f for f in slow_fake_ws.sent_frames if f.get("method") == "chat.abort"]
    assert len(aborts) >= 1
    assert "sessionKey" in aborts[0]["params"]
    assert "session" not in aborts[0]["params"]
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_completes_without_second_res(fake_ws):
    """Turn completion must come from chat.state=='final', not a second res frame —
    the real OpenClaw server (2026.6.11+) never sends one."""
    client = await connected_client(fake_ws)
    events = []
    async for event in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        events.append(event)
    res_frames_after_started = [
        f for f in fake_ws.sent_frames
        if f.get("method") == "chat.send"
    ]
    assert len(res_frames_after_started) == 1  # only the request itself was sent by us
    assert events[-1].type == "status"
    assert events[-1].content == "done"
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_yields_tool_call_events():
    fake_ws = SmartFakeWS(
        chat_responses={"agent:main:client-a": ["Listo."]},
        tool_calls={"agent:main:client-a": [
            {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}},
            {"phase": "result", "name": "exec", "toolCallId": "call-1", "isError": False,
             "result": {"content": [{"type": "text", "text": "file.txt"}]}},
        ]},
    )
    client = await connected_client(fake_ws)
    tool_events = []
    async for event in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        if event.type == "tool_call":
            tool_events.append(event.tool_call)
    assert len(tool_events) == 2
    assert tool_events[0].phase == "start"
    assert tool_events[0].name == "exec"
    assert tool_events[0].args == {"command": "ls"}
    assert tool_events[1].phase == "result"
    assert tool_events[1].result == "file.txt"
    assert tool_events[1].is_error is False
    await client.close()


@pytest.mark.asyncio
async def test_concurrent_stream_response_same_session_key_rejects_one():
    """Issue #99: two concurrent stream_response() calls sharing a session_key
    must not corrupt each other's queue. Reproduced via asyncio.gather, per the
    issue's repro steps — exactly one call is rejected immediately with the
    stable TURN_IN_PROGRESS_ERROR content, the other completes normally, and
    neither hangs."""
    fake_ws = SmartFakeWS({"agent:main:client-a": ["Hola ", "mundo"]})
    client = await connected_client(fake_ws)

    async def _consume():
        events = []
        async for event in client.stream_response(
            "hola", "client-a", session_key="agent:main:client-a"
        ):
            events.append(event)
        return events

    results = await asyncio.wait_for(
        asyncio.gather(_consume(), _consume()),
        timeout=1.0,
    )

    rejected = [r for r in results if r[0].type == "error"]
    succeeded = [r for r in results if r[0].type != "error"]
    assert len(rejected) == 1, f"expected exactly one rejected call, got: {results}"
    assert len(succeeded) == 1, f"expected exactly one successful call, got: {results}"

    assert len(rejected[0]) == 1  # rejected call yields exactly the error event, nothing else
    assert rejected[0][0].content == TURN_IN_PROGRESS_ERROR

    tokens = [e.content for e in succeeded[0] if e.type == "token"]
    assert tokens == ["Hola ", "mundo"]
    assert succeeded[0][-1].type == "status"
    assert succeeded[0][-1].content == "done"

    await client.close()


@pytest.mark.asyncio
async def test_connect_twice_closes_and_cancels_previous_connection():
    """Issue #103: connect() must tear down the previous ws/listener/keepalive
    before establishing a new one. Otherwise the old listener keeps dispatching
    frames from the old socket to the shared dispatcher, causing false
    error_all events on healthy sessions, and the old socket/keepalive leak."""
    fake_ws1 = SmartFakeWS({"agent:main:client-a": ["Hola"]})
    client = await connected_client(fake_ws1)
    old_listener_task = client._listener_task
    old_keepalive_task = client._keepalive_task
    assert not old_listener_task.done()

    disconnected = []
    client.on_disconnect = lambda: disconnected.append(True)

    fake_ws2 = SmartFakeWS({"agent:main:client-a": ["Hola"]})
    await fake_ws2.start()
    with patch("websockets.connect", return_value=fake_ws2):
        await client.connect()

    assert fake_ws1.closed, "previous socket must be closed on reconnect"
    assert old_listener_task.done(), "previous listener task must be cancelled"
    assert old_keepalive_task.done(), "previous keepalive task must be cancelled"
    assert client._listener_task is not old_listener_task
    assert not client._listener_task.done()

    # A forced reconnect legitimately replaces a healthy connection — cancelling
    # the old listener must not be mistaken for an unexpected drop, or
    # ReconnectingOpenClawClient would flip back into RECONNECTING right after
    # a successful forced reconnect.
    await asyncio.sleep(0.05)
    assert disconnected == []

    await client.close()


@pytest.mark.asyncio
async def test_concurrent_connect_calls_are_serialized():
    """Two connect() calls racing (e.g. an admin-triggered reconnect racing the
    background reconnect loop) must not interleave their handshakes — a
    _connect_lock serializes them so exactly one final ws/listener survives,
    with the other cleanly closed rather than orphaned."""
    client = make_client(SmartFakeWS())
    fake_ws_a = SmartFakeWS({"agent:main:client-a": ["Hola"]})
    fake_ws_b = SmartFakeWS({"agent:main:client-a": ["Hola"]})
    await fake_ws_a.start()
    await fake_ws_b.start()

    with patch("websockets.connect", side_effect=[fake_ws_a, fake_ws_b]):
        await asyncio.wait_for(
            asyncio.gather(client.connect(), client.connect()),
            timeout=1.0,
        )

    assert [fake_ws_a.closed, fake_ws_b.closed].count(True) == 1, (
        "exactly one of the two sockets must be closed as the loser of the race"
    )
    assert client._ws in (fake_ws_a, fake_ws_b)
    assert not client._listener_task.done()

    await client.close()


@pytest.mark.asyncio
async def test_admin_trigger_reconnect_coalesces_with_real_background_reconnect(fake_ws):
    """End-to-end version of the issue #103 coalescing guarantee: a REAL
    background reconnect (started by ReconnectingOpenClawClient after an
    unexpected drop) that is still mid-handshake must not be joined by a
    second, concurrent websocket handshake when an admin trigger_reconnect()
    call races it — it must coalesce onto the one in-flight attempt."""
    inner = await connected_client(fake_ws)
    roc = ReconnectingOpenClawClient(inner, "openclaw")  # rewires inner.on_disconnect

    reconnect_ws = SmartFakeWS({"agent:main:client-a": ["Hola"]})
    await reconnect_ws.start()
    connect_attempted = asyncio.Event()
    release_connect = asyncio.Event()
    connect_calls = 0

    async def gated_connect(uri):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            connect_attempted.set()
            await release_connect.wait()
        return reconnect_ws

    with patch("websockets.connect", side_effect=gated_connect):
        await fake_ws.close()  # simulate an unexpected drop → background reconnect starts
        await connect_attempted.wait()  # background reconnect is now mid-handshake

        admin_job_id = roc.trigger_reconnect()
        assert admin_job_id == roc._reconnect_job_id

        release_connect.set()
        await asyncio.sleep(0.05)

    assert connect_calls == 1, "admin trigger must coalesce, not open a second socket"
    assert roc.state == ConnectionState.CONNECTED
    await inner.close()


@pytest.mark.asyncio
async def test_concurrent_stream_response_different_session_keys_both_succeed():
    """Regression guard: rejecting duplicate session_keys must not affect
    unrelated sessions multiplexed on the same connection — both complete
    normally when run concurrently via asyncio.gather."""
    fake_ws = SmartFakeWS({
        "agent:main:client-a": ["Hola ", "mundo"],
        "agent:main:client-b": ["Adios ", "mundo"],
    })
    client = await connected_client(fake_ws)

    async def _consume(user_id, session_key):
        events = []
        async for event in client.stream_response("hola", user_id, session_key=session_key):
            events.append(event)
        return events

    events_a, events_b = await asyncio.wait_for(
        asyncio.gather(
            _consume("client-a", "agent:main:client-a"),
            _consume("client-b", "agent:main:client-b"),
        ),
        timeout=1.0,
    )

    tokens_a = [e.content for e in events_a if e.type == "token"]
    tokens_b = [e.content for e in events_b if e.type == "token"]
    assert tokens_a == ["Hola ", "mundo"]
    assert tokens_b == ["Adios ", "mundo"]
    assert events_a[-1].content == "done"
    assert events_b[-1].content == "done"

    await client.close()
