# tests/integration/test_openclaw_client.py
import asyncio
import json
import pytest
from unittest.mock import patch

from src.services.orchestrators.openclaw_client import OpenClawClient


def challenge_frame():
    return json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "abc", "ts": 0}})


def hello_ok_frame(req_id: str):
    return json.dumps({
        "type": "res", "id": req_id, "ok": True,
        "payload": {"type": "hello-ok", "protocol": 4, "policy": {"tickIntervalMs": 15000}}
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


@pytest.mark.asyncio
async def test_connect_handshake():
    """Client performs challenge → connect → hello-ok handshake."""
    connect_req_id = None

    async def fake_connect(uri, **kwargs):
        ws = FakeWebSocket([challenge_frame()])
        # Capture the connect req_id so we can build hello-ok
        original_send = ws.send
        async def capturing_send(data):
            nonlocal connect_req_id
            frame = json.loads(data)
            if frame.get("method") == "connect":
                connect_req_id = frame["id"]
                # Inject hello-ok into the queue
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
async def test_stream_response_yields_tokens():
    """stream_response yields OrchestratorEvent tokens from chat events."""
    req_id_holder = {}

    class SmartFakeWS(FakeWebSocket):
        def __init__(self):
            self._queue = asyncio.Queue()
            self.sent = []
            # Pre-load handshake frames
            self._handshake = iter([challenge_frame()])
            self._handshake_done = False

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
                await self._queue.put(hello_ok_frame(req_id))
            elif method == "chat.send":
                req_id_holder["id"] = req_id
                # Send two chat deltas then the res
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

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self._queue.get()

        async def close(self): pass

    fake_ws = SmartFakeWS()

    async def fake_connect_async(uri, **kwargs):
        return fake_ws

    with patch("websockets.connect", side_effect=fake_connect_async):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()

        events = []
        async for event in client.stream_response(text="Hi", user_id="test"):
            events.append(event)

        await client.close()

    tokens = [e for e in events if e.type == "token"]
    status = [e for e in events if e.type == "status"]
    assert [t.content for t in tokens] == ["Hello", " world"]
    assert len(status) == 1
    assert status[0].content == "done"


@pytest.mark.asyncio
async def test_ping_returns_true_on_ok_response():
    """ping() sends health req and returns True when res.ok is True."""
    class HealthFakeWS(FakeWebSocket):
        def __init__(self):
            self._queue = asyncio.Queue()
            self.sent = []
            self._handshake = [challenge_frame()]
            self._idx = 0

        async def recv(self):
            if self._idx < len(self._handshake):
                val = self._handshake[self._idx]
                self._idx += 1
                return val
            return await self._queue.get()

        async def send(self, data):
            frame = json.loads(data)
            self.sent.append(frame)
            if frame.get("method") == "connect":
                await self._queue.put(hello_ok_frame(frame["id"]))
            elif frame.get("method") == "health":
                await self._queue.put(json.dumps({"type": "res", "id": frame["id"], "ok": True, "payload": {}}))

        def __aiter__(self): return self
        async def __anext__(self): return await self._queue.get()
        async def close(self): pass

    fake_ws = HealthFakeWS()

    async def fake_connect_async(uri, **kwargs):
        return fake_ws

    with patch("websockets.connect", side_effect=fake_connect_async):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()
        result = await client.ping()
        await client.close()

    assert result is True
