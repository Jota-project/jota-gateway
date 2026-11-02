"""TTSClient lee el socket por delante del consumidor, con tope de 10 MB (7e)."""

import asyncio
import logging

import pytest

from src.services import tts_client
from src.services.tts_client import TTSClient
from tests.fakes.jota_upstreams import FakeTts


@pytest.fixture
async def fake():
    f = FakeTts(chunks_per_reply=20)
    await f.start()
    yield f
    await f.stop()


async def _client(fake: FakeTts) -> TTSClient:
    c = TTSClient(f"127.0.0.1:{fake.port}", "tok", "t")
    await c.connect()
    return c


async def test_audio_is_read_ahead_of_a_slow_consumer(fake):
    c = await _client(fake)
    try:
        await c.end()
        stream = c.get_audio_stream()
        await stream.__anext__()
        await asyncio.sleep(0.2)
        assert c._buffered == 19 * len(fake.chunk)
        await stream.aclose()
    finally:
        await c.close()


async def test_buffer_cap_ends_the_stream(fake, monkeypatch, caplog):
    monkeypatch.setattr(tts_client, "MAX_BUFFER_BYTES", 3 * len(fake.chunk))
    c = await _client(fake)
    try:
        await c.end()
        with caplog.at_level(logging.WARNING):
            stream = c.get_audio_stream()
            chunks = [await stream.__anext__()]
            await asyncio.sleep(0.2)  # consumidor lento: la cola llega al tope
            chunks += [x async for x in stream]
        assert len(chunks) <= 4
        assert any("búfer de audio" in r.getMessage() for r in caplog.records)
        assert not any(fake.chunk.decode("latin1") in r.getMessage() for r in caplog.records)
    finally:
        await c.close()


async def test_close_stops_the_reader(fake):
    c = await _client(fake)
    await c.end()
    stream = c.get_audio_stream()
    await stream.__anext__()
    reader = c._reader
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await c.close()
    assert loop.time() - t0 < 1
    assert reader is not None and reader.done()
