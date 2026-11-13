import pytest

from fogar_gateway.services.pacing import BYTES_PER_S, FRAME_BYTES, AudioPacer


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.now += d


def make(lead_s: float = 2.0, enabled: bool = True) -> tuple[AudioPacer, FakeTime]:
    t = FakeTime()
    return AudioPacer(lead_s, enabled, clock=t.clock, sleep=t.sleep), t


async def collect(pacer: AudioPacer, chunk: bytes) -> list[bytes]:
    return [f async for f in pacer.frames(chunk)]


@pytest.mark.asyncio
async def test_disabled_yields_chunk_unchanged_without_sleeping():
    pacer, t = make(enabled=False)
    chunk = b"x" * 30_000
    assert await collect(pacer, chunk) == [chunk]
    assert t.sleeps == []


@pytest.mark.asyncio
async def test_splits_into_200ms_frames():
    pacer, _ = make()
    chunk = (bytes(range(256)) * 118)[:30_000]
    frames = await collect(pacer, chunk)
    assert [len(f) for f in frames] == [9600, 9600, 9600, 1200]
    assert b"".join(frames) == chunk


@pytest.mark.asyncio
async def test_odd_length_chunk_loses_no_byte():
    pacer, _ = make()
    chunk = b"y" * 9601
    frames = await collect(pacer, chunk)
    assert [len(f) for f in frames] == [9600, 1]
    assert b"".join(frames) == chunk


@pytest.mark.asyncio
async def test_initial_burst_then_never_more_than_lead_ahead():
    pacer, t = make(lead_s=2.0)
    produced = 0
    for _ in range(10):
        async for f in pacer.frames(b"z" * 48_000):
            produced += len(f)
            assert pacer._play_end - t.now <= 2.0 + 0.2
            if produced / BYTES_PER_S <= 2.0:
                assert t.sleeps == []
    assert sum(t.sleeps) == pytest.approx(10 - 2 - 0.2, abs=0.25)


@pytest.mark.asyncio
async def test_no_credit_after_a_gap():
    pacer, t = make(lead_s=2.0)
    await collect(pacer, b"a" * BYTES_PER_S)  # 1 s, no sleeps (within lead)
    t.now += 5.0  # slow TTS: the buffer drained
    n = len(t.sleeps)
    first = [f async for f in pacer.frames(b"b" * FRAME_BYTES)]
    assert len(first) == 1 and len(t.sleeps) == n
    await collect(pacer, b"c" * (10 * BYTES_PER_S))
    assert pacer._play_end - t.now <= 2.0 + 0.2
