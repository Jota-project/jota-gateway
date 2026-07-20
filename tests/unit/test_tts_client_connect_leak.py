"""Tests for TTSClient.connect() socket leak on handshake failure (issue #96)."""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock
from websockets.exceptions import ConnectionClosed

from src.services.tts_client import TTSClient
from src.core.config import settings


class FakeTTSSocket:
    """Fake websocket that records close() calls and simulates TTS handshake failure."""
    def __init__(self, recv_payloads=None):
        self._to_client = asyncio.Queue()
        for p in recv_payloads or []:
            self._to_client.put_nowait(p)
        self.recv_started = asyncio.Event()
        self.closed = False
        self.close_code = None
        self.sent_frames = []

    async def send(self, data):
        self.sent_frames.append(json.loads(data))

    async def recv(self):
        self.recv_started.set()
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

    tts = TTSClient(url="test:1", token="bad", client_id="c1")
    with patch("websockets.connect", side_effect=fake_connect):
        with pytest.raises(RuntimeError) as exc_info:
            await tts.connect()

    assert "bad_token" in str(exc_info.value)
    assert fake_ws.closed, "Socket must be closed when auth fails"
    assert tts.ws is None


@pytest.mark.asyncio
async def test_connect_closes_socket_and_reraises_cancelled_error():
    fake_ws = FakeTTSSocket()

    async def fake_connect(url):
        return fake_ws

    tts = TTSClient(url="test:1", token="token", client_id="c1")
    with patch("websockets.connect", side_effect=fake_connect):
        task = asyncio.create_task(tts.connect())
        await fake_ws.recv_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert fake_ws.closed, "Socket must be closed when auth is cancelled"
    assert tts.ws is None


@pytest.mark.asyncio
async def test_connect_uses_configured_auth_timeout_and_closes_socket(monkeypatch):
    fake_ws = FakeTTSSocket()

    async def fake_connect(url):
        return fake_ws

    monkeypatch.setattr(settings, "TTS_AUTH_TIMEOUT_S", 0.01)
    tts = TTSClient(url="test:1", token="token", client_id="c1")

    with patch("websockets.connect", side_effect=fake_connect):
        with patch(
            "src.services.tts_client.asyncio.wait_for",
            wraps=asyncio.wait_for,
        ) as wait_for:
            with pytest.raises(asyncio.TimeoutError):
                await tts.connect()

    wait_for.assert_awaited_once()
    assert wait_for.await_args.kwargs["timeout"] == 0.01
    assert fake_ws.closed, "Socket must be closed when auth times out"
    assert tts.ws is None
