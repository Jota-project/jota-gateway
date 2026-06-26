# tests/integration/test_openclaw_client.py
import asyncio
import json
import pytest
from unittest.mock import patch

from src.services.orchestrators.openclaw_client import OpenClawClient


def challenge_frame():
    return json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "abc", "ts": 0}})


def hello_ok_frame(req_id: str, tick_interval_ms: int = 15000):
    return json.dumps({
        "type": "res", "id": req_id, "ok": True,
        "payload": {"type": "hello-ok", "protocol": 4, "policy": {"tickIntervalMs": tick_interval_ms}}
    })


class FakeWebSocket:
    """Simulates a WebSocket server for testing OpenClawClient."""

    def __init__(self, recv_sequence: list[str]):
        self._recv_iter = iter(recv_sequence)
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        return next(self._recv_iter)

    async def close(self) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._recv_iter)
        except StopIteration:
            raise StopAsyncIteration


def fake_connect(fake_ws):
    """Return a side_effect coroutine that yields fake_ws from websockets.connect."""
    async def _connect(uri, **kwargs):
        return fake_ws
    return _connect


class SmartFakeWS(FakeWebSocket):
    """Queue-backed fake WebSocket that auto-responds to handshake and chat.send."""

    def __init__(self, tick_interval_ms: int = 15000):
        self._queue = asyncio.Queue()
        self.sent: list[dict] = []
        self._handshake = iter([challenge_frame()])
        self._handshake_done = False
        self._tick_interval_ms = tick_interval_ms

    async def recv(self):
        if not self._handshake_done:
            try:
                return next(self._handshake)
            except StopIteration:
                self._handshake_done = True
        return await self._queue.get()

    async def send(self, data):
        frame = json.loads(data)
        self.sent.append(frame)
        method = frame.get("method")
        req_id = frame.get("id")
        if method == "connect":
            await self._queue.put(hello_ok_frame(req_id, self._tick_interval_ms))
        elif method == "health":
            await self._queue.put(json.dumps({"type": "res", "id": req_id, "ok": True, "payload": {}}))
        # chat.send and chat.abort handled per-test via subclassing

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._queue.get()

    async def close(self): pass


@pytest.mark.asyncio
async def test_connect_handshake():
    """Client performs challenge → connect → hello-ok handshake."""
    connect_req_id = None

    async def fake_connect(uri, **kwargs):
        ws = FakeWebSocket([challenge_frame()])
        original_send = ws.send
        async def capturing_send(data):
            nonlocal connect_req_id
            frame = json.loads(data)
            if frame.get("method") == "connect":
                connect_req_id = frame["id"]
                ws._recv_iter = iter([hello_ok_frame(connect_req_id)])
            await original_send(data)
        ws.send = capturing_send
        return ws

    with patch("websockets.connect", side_effect=fake_connect):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()
        assert client._ws is not None
        await client.close()


@pytest.mark.asyncio
async def test_connect_sets_tick_interval_from_policy():
    """connect() parses tickIntervalMs from hello-ok policy."""
    fake_ws = SmartFakeWS(tick_interval_ms=20000)

    with patch("websockets.connect", side_effect=fake_connect(fake_ws)):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()
        assert client._tick_interval == 20.0
        await client.close()


@pytest.mark.asyncio
async def test_stream_response_uses_session_key_format():
    """chat.send must use session: {key: ...} not sessionKey (protocol v4)."""

    class SessionKeyFakeWS(SmartFakeWS):
        async def send(self, data):
            frame = json.loads(data)
            self.sent.append(frame)
            method = frame.get("method")
            req_id = frame.get("id")
            if method == "connect":
                await self._queue.put(hello_ok_frame(req_id))
            elif method == "chat.send":
                await self._queue.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True, "payload": {}
                }))

    fake_ws = SessionKeyFakeWS()

    with patch("websockets.connect", side_effect=fake_connect(fake_ws)):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token", default_agent="my-agent")
        await client.connect()
        async for _ in client.stream_response(text="Hi", user_id="test", session_key="agent:my-agent:client-42"):
            pass
        await client.close()

    chat_send = next(f for f in fake_ws.sent if f.get("method") == "chat.send")
    params = chat_send["params"]
    assert "session" in params, "params must have 'session' key"
    assert params["session"] == {"key": "agent:my-agent:client-42"}, "session key must match what caller passed"
    assert "sessionKey" not in params, "flat sessionKey is wrong (protocol v4 uses session.key)"


@pytest.mark.asyncio
async def test_stream_response_yields_tokens():
    """stream_response yields OrchestratorEvent tokens from chat events."""

    class TokenFakeWS(SmartFakeWS):
        async def send(self, data):
            frame = json.loads(data)
            self.sent.append(frame)
            method = frame.get("method")
            req_id = frame.get("id")
            if method == "connect":
                await self._queue.put(hello_ok_frame(req_id))
            elif method == "chat.send":
                await self._queue.put(json.dumps({
                    "type": "event", "event": "chat",
                    "payload": {"deltaText": "Hello", "replace": False, "seq": 1}
                }))
                await self._queue.put(json.dumps({
                    "type": "event", "event": "chat",
                    "payload": {"deltaText": " world", "replace": False, "seq": 2}
                }))
                await self._queue.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True, "payload": {}
                }))

    fake_ws = TokenFakeWS()

    with patch("websockets.connect", side_effect=fake_connect(fake_ws)):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()

        events = []
        async for event in client.stream_response(text="Hi", user_id="test", session_key="agent:main:test-client"):
            events.append(event)

        await client.close()

    tokens = [e for e in events if e.type == "token"]
    status = [e for e in events if e.type == "status"]
    assert [t.content for t in tokens] == ["Hello", " world"]
    assert len(status) == 1
    assert status[0].content == "done"


@pytest.mark.asyncio
async def test_stream_response_sends_abort_on_cancellation():
    """When the consuming task is cancelled mid-turn, chat.abort is sent to OpenClaw."""

    class AbortFakeWS(SmartFakeWS):
        async def send(self, data):
            frame = json.loads(data)
            self.sent.append(frame)
            method = frame.get("method")
            req_id = frame.get("id")
            if method == "connect":
                await self._queue.put(hello_ok_frame(req_id))
            elif method == "chat.send":
                # Send one token but never complete the turn
                await self._queue.put(json.dumps({
                    "type": "event", "event": "chat",
                    "payload": {"deltaText": "Partial...", "replace": False, "seq": 1}
                }))
            # chat.abort — no response needed, just record

    fake_ws = AbortFakeWS()

    with patch("websockets.connect", side_effect=fake_connect(fake_ws)):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token", default_agent="main")
        await client.connect()

        # Cancel the consuming task after receiving the first token
        received = []
        async def consume():
            async for event in client.stream_response(text="Hi", user_id="test", session_key="test-sess"):
                received.append(event)
                if len(received) == 1:
                    raise asyncio.CancelledError()

        task = asyncio.create_task(consume())
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Give asyncio.shield a chance to complete
        await asyncio.sleep(0.05)
        await client.close()

    abort_frames = [f for f in fake_ws.sent if f.get("method") == "chat.abort"]
    assert len(abort_frames) == 1
    assert abort_frames[0]["params"] == {"session": {"key": "test-sess"}}


@pytest.mark.asyncio
async def test_ping_returns_true_on_ok_response():
    """ping() sends health req and returns True when res.ok is True."""
    fake_ws = SmartFakeWS()

    with patch("websockets.connect", side_effect=fake_connect(fake_ws)):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()
        result = await client.ping()
        await client.close()

    assert result is True
