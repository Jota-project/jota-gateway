"""Contrato de todo TextToSpeechProvider (se ejecuta por adaptador)."""

import asyncio
import logging
import time


async def _collect(stream):
    return [c async for c in stream.get_audio_stream()]


async def test_incremental_text_yields_canonical_audio(tts_harness):
    stream = await tts_harness.provider.connect("af_heart", 1.0, "c1")
    assert stream is not None
    try:
        for piece in ("Hola ", "qué ", "tal."):
            await stream.send_text_chunk(piece)
        await stream.end()
        chunks = await asyncio.wait_for(_collect(stream), 2)
    finally:
        await stream.close()
    assert chunks and all(isinstance(c, bytes) and c for c in chunks)
    assert all(len(c) % 2 == 0 for c in chunks)  # PCM16


async def test_end_terminates_audio_iterator(tts_harness):
    stream = await tts_harness.provider.connect(None, None, "c1")
    try:
        await stream.send_text_chunk("x")
        await stream.end()
        await asyncio.wait_for(_collect(stream), 2)  # no cuelga
    finally:
        await stream.close()


async def test_close_midway_returns_quickly(tts_harness):
    stream = await tts_harness.provider.connect(None, None, "c1")
    await stream.send_text_chunk("a medias")
    t0 = time.monotonic()
    await stream.close()
    assert time.monotonic() - t0 < 1.0


async def test_connect_returns_none_when_backend_down(tts_harness):
    assert await tts_harness.backend_down_provider.connect(None, None, "c1") is None


async def test_no_secret_in_logs(tts_harness, caplog):
    from tests.contract.conftest import SECRET_TTS_TOKEN, USER_TEXT

    with caplog.at_level(logging.DEBUG):
        stream = await tts_harness.provider.connect("v", 1.0, "c1")
        await stream.send_text_chunk(USER_TEXT)
        await stream.end()
        await asyncio.wait_for(_collect(stream), 2)
        await stream.close()
        assert await tts_harness.backend_down_provider.connect(None, None, "c2") is None
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET_TTS_TOKEN not in logged
    assert USER_TEXT not in logged
