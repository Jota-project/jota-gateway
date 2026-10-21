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


async def _failed_attempt(provider, client_id):
    """Un intento contra un backend caído no da audio. Un adaptador puede devolver `None` ya
    (jota, que espera a la red) o un stream sin audio (los que conectan en segundo plano)."""
    stream = await provider.connect(None, None, client_id)
    if stream is not None:
        await stream.send_text_chunk("hola.")
        await stream.end()
        assert await asyncio.wait_for(_collect(stream), 5) == []
        await stream.close()


async def test_connect_yields_no_audio_and_backs_off_when_backend_down(tts_harness):
    provider = tts_harness.backend_down_provider
    await _failed_attempt(provider, "c1")
    # el fallo queda registrado: dentro de la ventana de backoff no se intenta ni la red
    assert await provider.connect(None, None, "c1") is None


async def test_no_secret_in_logs(tts_harness, caplog):
    from tests.contract.conftest import USER_TEXT

    with caplog.at_level(logging.DEBUG):
        stream = await tts_harness.provider.connect("v", 1.0, "c1")
        await stream.send_text_chunk(USER_TEXT)
        await stream.end()
        await asyncio.wait_for(_collect(stream), 2)
        await stream.close()
        await _failed_attempt(tts_harness.backend_down_provider, "c2")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert tts_harness.token not in logged
    assert USER_TEXT not in logged
