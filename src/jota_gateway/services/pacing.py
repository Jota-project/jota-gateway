"""Paces TTS audio to playback rate with a small lead (block 7e)."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

BYTES_PER_S = 48000  # PCM16 LE, 24 kHz, mono
FRAME_BYTES = 9600  # 200 ms


class AudioPacer:
    """One per turn: yields frames, sleeping so audio is at most `lead_s` ahead of playback."""

    def __init__(
        self,
        lead_s: float,
        enabled: bool,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._lead_s = lead_s
        self._enabled = enabled
        self._clock = clock
        self._sleep = sleep
        self._play_end = 0.0

    async def frames(self, chunk: bytes) -> AsyncIterator[bytes]:
        if not self._enabled:
            yield chunk
            return
        for i in range(0, len(chunk), FRAME_BYTES):
            frame = chunk[i : i + FRAME_BYTES]
            now = self._clock()
            start = max(self._play_end, now)  # no credit after a gap
            if start - now > self._lead_s:
                await self._sleep(start - now - self._lead_s)
            self._play_end = start + len(frame) / BYTES_PER_S
            yield frame
