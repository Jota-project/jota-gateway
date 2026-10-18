"""G-01 (#214): TranscriberClient.close() must really close the socket.

Uses a real `websockets` server on port 0: the old fake exposed `.closed`, an
attribute that does not exist in `websockets.asyncio`, which hid the bug.
"""

import asyncio
import json

import websockets

from src.services.transcriber_client import TranscriberClient


async def test_close_closes_real_socket_and_server_sees_it():
    closed = asyncio.Event()

    async def handler(ws):
        await ws.recv()  # config handshake
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        try:
            async for _ in ws:
                pass
        finally:
            closed.set()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = TranscriberClient(url=f"127.0.0.1:{port}", client_id="c1")
        await client.connect()
        assert client._is_ready

        await client.close()

        await asyncio.wait_for(closed.wait(), timeout=2.0)
        assert client.ws.state.name == "CLOSED"


async def test_close_logs_failure_instead_of_swallowing(caplog):
    class BrokenWS:
        async def close(self):
            raise OSError("boom")

    client = TranscriberClient(url="x:1", client_id="c1")
    client.ws = BrokenWS()
    with caplog.at_level("WARNING"):
        await client.close()  # must not raise
    assert any("OSError" in r.getMessage() for r in caplog.records)
