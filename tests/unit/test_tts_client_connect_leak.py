"""Tests for TTSClient.connect() socket leak on handshake failure (issue #96)."""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock
from websockets.exceptions import ConnectionClosed

from src.services.tts_client import TTSClient


class FakeTTSSocket:
    """Fake websocket that records close() calls and simulates TTS handshake failure."""
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
async def test_connect_closes_socket_when_auth_fails():
    """If websockets.connect succeeds but TTS auth fails, the socket must
    be closed before re-raising — otherwise it leaks (issue #96)."""
    fake_ws = FakeTTSSocket(recv_payloads=[
        json.dumps({"type": "error", "code": "bad_token", "message": "invalid"})
    ])

    async def fake_connect(url):
        return fake_ws

    with patch("websockets.connect", side_effect=fake_connect):
        with pytest.raises(RuntimeError) as exc_info:
            await TTSClient(url="test:1", token="bad", client_id="c1").connect()

    assert "bad_token" in str(exc_info.value)
    assert fake_ws.closed, "Socket must be closed when auth fails"
