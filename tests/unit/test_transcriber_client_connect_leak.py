"""Tests for TranscriberClient.connect() socket leak on handshake failure (issue #96)."""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock
from websockets.exceptions import ConnectionClosed

from src.services.transcriber_client import TranscriberClient


class FakeSocket:
    """Fake websocket that records close() calls and simulates handshake failure."""
    def __init__(self, recv_payloads=None):
        self._to_client = asyncio.Queue()
        for p in recv_payloads or []:
            self._to_client.put_nowait(p)
        self.closed = False
        self.close_code = None
        self.sent_frames = []

    async def send(self, data):
        self.sent_frames.append(json.loads(data))

    async def recv(self):
        val = await self._to_client.get()
        if val is None:
            raise ConnectionClosed(rcvd=MagicMock(code=1000))
        return val

    async def close(self, code=None):
        self.closed = True
        self.close_code = code

    def __await__(self):
        async def _():
            return self
        return _().__await__()


@pytest.mark.asyncio
async def test_connect_closes_socket_when_handshake_fails():
    """If websockets.connect succeeds but the handshake fails, the socket must
    be closed before re-raising — otherwise it leaks (issue #96)."""
    fake_ws = FakeSocket(recv_payloads=[
        json.dumps({"type": "error", "code": "auth_failed", "message": "bad token"})
    ])

    with patch("websockets.connect", side_effect=lambda url: fake_ws):
        with pytest.raises(Exception) as exc_info:
            await TranscriberClient(url="test:1", client_id="c1").connect()

    assert "auth_failed" in str(exc_info.value)
    assert fake_ws.closed, "Socket must be closed when handshake fails"
