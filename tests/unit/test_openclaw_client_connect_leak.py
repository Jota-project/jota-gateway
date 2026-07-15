"""Tests for OpenClawClient.connect() socket leak on handshake failure (issue #96)."""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock
from websockets.exceptions import ConnectionClosed

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import TurnRegistry, ClientRegistry


class FakeOCSocket:
    """Fake websocket that simulates OpenClaw handshake failure at a given point."""
    def __init__(self, fail_at="challenge", fail_with=None):
        self._to_client = asyncio.Queue()
        self.closed = False
        self.close_code = None
        self.sent_frames = []
        self._fail_at = fail_at
        self._fail_with = fail_with or {}
        self._step = 0

    async def send(self, data):
        self.sent_frames.append(json.loads(data))

    async def recv(self):
        self._step += 1
        if self._fail_at == "challenge":
            raise RuntimeError("challenge failed")
        elif self._fail_at == "hello_ok" and self._step == 2:
            raise RuntimeError("hello_ok failed")
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
async def test_connect_closes_socket_when_challenge_fails():
    """If websockets.connect succeeds but the challenge step fails, the socket
    must be closed before re-raising — otherwise it leaks (issue #96)."""
    fake_ws = FakeOCSocket(fail_at="challenge")

    turn_reg = TurnRegistry()
    dispatcher = FrameDispatcher(turn_registry=turn_reg, client_registry=ClientRegistry())

    async def fake_connect(uri):
        return fake_ws

    with patch("websockets.connect", side_effect=fake_connect):
        with pytest.raises(RuntimeError):
            await OpenClawClient(
                host="test", port=1, token="x",
                turn_registry=turn_reg, dispatcher=dispatcher
            ).connect()

    assert fake_ws.closed, "Socket must be closed when challenge fails"
