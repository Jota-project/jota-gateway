"""G-40 (#216): ni los logs ni los mensajes de excepción de los clientes de transcriber/TTS
llevan el contenido de un frame (puede incluir transcripción). Solo tipo o razón fija."""

import json
import logging

import pytest
import websockets

from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

SECRET = "TRANSCRIPCION-PRIVADA-hola-que-tal"


def _tts(handler):
    return websockets.serve(handler, "127.0.0.1", 0)


async def test_tts_non_json_during_auth_does_not_leak_frame():
    async def handler(ws):
        await ws.recv()
        await ws.send(SECRET)

    async with _tts(handler) as server:
        port = server.sockets[0].getsockname()[1]
        tts = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        with pytest.raises(RuntimeError) as exc:
            await tts.connect()
    assert SECRET not in str(exc.value)


async def test_tts_auth_failed_does_not_leak_message():
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "error", "detail": SECRET}))

    async with _tts(handler) as server:
        port = server.sockets[0].getsockname()[1]
        tts = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        with pytest.raises(RuntimeError) as exc:
            await tts.connect()
    assert SECRET not in str(exc.value)


async def test_tts_unparseable_frame_does_not_leak(caplog):
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.send(SECRET)
        await ws.send(json.dumps({"type": "done"}))

    async with _tts(handler) as server:
        port = server.sockets[0].getsockname()[1]
        tts = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        await tts.connect()
        with caplog.at_level(logging.DEBUG):
            [c async for c in tts.get_audio_stream()]
        await tts.close()
    assert SECRET not in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_transcriber_non_json_frame_does_not_leak(caplog):
    async def frames():
        yield SECRET

    client = TranscriberClient(url="ws://test", client_id="test")
    client.ws = frames()

    async def cb(text, is_final):
        pass

    with caplog.at_level(logging.DEBUG):
        await client.listen_loop(on_transcription_callback=cb)
    assert SECRET not in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)
