"""G-66 (#214): TTS frames that are valid JSON but not an object must not kill the turn.

Real `websockets` server on port 0 playing the TTS role.
"""

import json
import logging

import websockets

from src.services.tts_client import TTSClient


def _serve(frames_after_auth):
    async def handler(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "auth_ok"}))
        for f in frames_after_auth:
            await ws.send(f)

    return websockets.serve(handler, "127.0.0.1", 0)


async def test_get_audio_stream_skips_non_dict_frames(caplog):
    frames = ["42", "[]", '"hola secreto"', b"\x01\x02", json.dumps({"type": "done"})]
    async with _serve(frames) as server:
        port = server.sockets[0].getsockname()[1]
        tts = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        await tts.connect()
        with caplog.at_level(logging.WARNING):
            chunks = [c async for c in tts.get_audio_stream()]
        await tts.close()

    assert chunks == [b"\x01\x02"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("int" in w for w in warnings) and any("list" in w for w in warnings)
    assert not any("secreto" in w or "42" in w for w in warnings)  # type only, never content


async def test_connect_non_dict_auth_reply_raises_runtime_error():
    async def handler(ws):
        await ws.recv()
        await ws.send("42")

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        tts = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        try:
            await tts.connect()
        except RuntimeError as e:
            assert "42" not in str(e)
        else:
            raise AssertionError("connect must fail")


async def test_error_frame_code_is_sanitized_in_logs(caplog):
    """#260: el `code` de un frame de error del TTS es texto ajeno: nunca al log tal cual."""
    frames = [json.dumps({"type": "error", "code": "MARKER texto del proveedor"})]
    async with _serve(frames) as server:
        port = server.sockets[0].getsockname()[1]
        tts = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        await tts.connect()
        with caplog.at_level(logging.DEBUG):
            assert [c async for c in tts.get_audio_stream()] == []
        await tts.close()
    assert "MARKER" not in caplog.text
    assert "TTS error: unknown" in caplog.text
