"""#222: what TTSClient and TranscriberClient do when their socket goes away.

Real `websockets` servers on port 0, as in the other protocol client tests: the
socket closes for real, so the client sees the same `ConnectionClosed` it would
see in production.
"""

import asyncio
import json

import pytest
import websockets

from fogar_gateway.services.transcriber_client import TranscriberClient
from fogar_gateway.services.tts_client import TTSClient


def _port(server) -> int:
    return server.sockets[0].getsockname()[1]


# ── TTS ──────────────────────────────────────────────────────────────────────


async def test_tts_audio_stream_ends_quietly_when_the_server_drops_mid_turn():
    async def handler(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.send(b"\x01\x02")
        await ws.send(json.dumps({"type": "progress"}))  # control frame: skipped
        await ws.send(b"\x03\x04")
        await ws.close(1011)  # no `done`: the TTS died

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        tts = TTSClient(url=f"127.0.0.1:{_port(server)}", token="t", client_id="c1")
        await tts.connect()
        chunks = [c async for c in tts.get_audio_stream()]
        # The socket is gone: sending more of the turn must not raise either.
        await tts.send_text_chunk("hola")
        await tts.end()
        await tts.close()

    assert chunks == [b"\x01\x02", b"\x03\x04"]


async def test_tts_without_socket_is_a_no_op():
    tts = TTSClient(url="x:1", token="t", client_id="c1")
    await tts.send_text_chunk("hola")
    await tts.end()
    assert [c async for c in tts.get_audio_stream()] == []
    await tts.close()


async def test_tts_close_failure_is_logged_not_raised(caplog):
    class BrokenWS:
        async def close(self, code):
            raise OSError("boom")

    tts = TTSClient(url="x:1", token="t", client_id="c1")
    tts.ws = BrokenWS()
    with caplog.at_level("DEBUG"):
        await tts.close()
    assert "OSError" in caplog.text


# ── Transcriber ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("code", "dropped"), [(1011, True), (1000, False)])
async def test_transcriber_listen_loop_flags_only_an_unexpected_drop(code, dropped):
    async def handler(ws):
        await ws.recv()  # config handshake
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        await ws.close(code)

    async def on_transcription(text, is_final):
        raise AssertionError("no transcription expected")

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        client = TranscriberClient(url=f"127.0.0.1:{_port(server)}", client_id="c1")
        await client.connect()
        await asyncio.wait_for(client.listen_loop(on_transcription), timeout=2.0)

    assert client._dropped_unexpectedly is dropped
    if dropped:
        assert not client._is_ready


async def test_transcriber_send_on_a_closed_socket_marks_it_not_ready():
    gone = asyncio.Event()

    async def handler(ws):
        await ws.recv()  # config handshake
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        await ws.close(1011)
        gone.set()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        client = TranscriberClient(url=f"127.0.0.1:{_port(server)}", client_id="c1")
        await client.connect()
        await asyncio.wait_for(gone.wait(), timeout=2.0)
        await asyncio.wait_for(client.ws.wait_closed(), timeout=2.0)
        await client.send_audio(b"\x00\x00")
        assert not client._is_ready

        client._is_ready = True  # same for the end-of-audio signal
        await client.send_end()
        assert not client._is_ready
        await client.close()


async def test_transcriber_without_socket_is_a_no_op():
    client = TranscriberClient(url="x:1", client_id="c1")
    await client.send_audio(b"\x00\x00")
    await client.send_end()
    assert not client._is_ready
