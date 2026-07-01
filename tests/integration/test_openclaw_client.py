import asyncio
import json
import uuid
from typing import Optional
from unittest.mock import patch

import pytest

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import TurnRegistry, ClientRegistry

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


class SmartFakeWS:
    """Queue-backed fake WebSocket that auto-responds to OpenClaw protocol v4.

    chat_responses: {sessionKey → [list of deltaText strings]}
    """

    def __init__(self, chat_responses: Optional[dict] = None):
        self.chat_responses: dict[str, list[str]] = chat_responses or {}
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
                    "payload": HELLO_OK_PAYLOAD,
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
                for i, chunk in enumerate(chunks):
                    await self._to_client.put(json.dumps({
                        "type": "event", "event": "chat",
                        "payload": {
                            "runId": run_id, "sessionKey": sk,
                            "seq": i + 1, "state": "delta", "deltaText": chunk,
                        },
                    }))
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"status": "done", "runId": run_id},
                }))

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
    client = await connected_client(fake_ws)

    async def consume():
        async for _ in client.stream_response(
            "hola", "client-a", session_key="agent:main:client-a"
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.05)  # let shield-wrapped abort send complete

    aborts = [f for f in fake_ws.sent_frames if f.get("method") == "chat.abort"]
    assert len(aborts) >= 1
    assert "sessionKey" in aborts[0]["params"]
    assert "session" not in aborts[0]["params"]
    await client.close()
