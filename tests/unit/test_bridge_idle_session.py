"""#227: sesiones persistentes inactivas (cliente con wake word) y el watchdog de silencio."""

import asyncio
import heapq
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.bridge as bridge_module
from src.models.schemas import ClientConfig
from src.services.reconnection import ConnectionState
from tests.fakes.bridge import sent


@pytest.fixture
def make_idle_bridge(make_bridge):
    def _make(config=None):
        t = MagicMock()
        t.state = ConnectionState.CONNECTED
        t.last_transcript_at = None
        t.send_audio = AsyncMock()
        t.send_end = AsyncMock()
        bridge, ws = make_bridge(config=config or ClientConfig(), input_mode="audio", transcriber=t)
        return bridge, ws

    return _make


def _text(obj_or_str):
    s = obj_or_str if isinstance(obj_or_str, str) else json.dumps(obj_or_str)
    return {"type": "websocket.receive", "text": s}


# Virtual time (#266): the bridge's `time.monotonic` and `_sleep` only advance
# when the test calls `advance`, so the result never depends on how busy the machine is.


class VClock:
    def __init__(self, monkeypatch):
        self.t = 1000.0
        self._timers: list = []
        self._seq = 0
        monkeypatch.setattr(bridge_module, "time", SimpleNamespace(monotonic=lambda: self.t))
        monkeypatch.setattr(bridge_module, "_sleep", self.sleep)

    async def sleep(self, d):
        fut = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._timers, (self.t + d, self._seq, fut))
        await fut

    @staticmethod
    async def _settle():
        for _ in range(50):  # let every ready task run to its next timer/queue wait
            await asyncio.sleep(0)

    async def advance(self, until):
        await self._settle()
        while self._timers and self._timers[0][0] <= until:
            deadline, _, fut = heapq.heappop(self._timers)
            self.t = max(self.t, deadline)
            if not fut.done():
                fut.set_result(None)
            await self._settle()
        self.t = max(self.t, until)


def _audio():
    return {"type": "websocket.receive", "bytes": b"\x00\x00\x00\x00"}


async def _feed(clock, ws, *, audio_for=0.0, keepalive_for=0.0, every=0.01):
    for frame, duration in ((_audio, audio_for), (lambda: _text({"type": "keepalive"}), keepalive_for)):
        end = clock.t + duration
        while clock.t < end:
            ws.inbox.put_nowait(frame())
            await clock.advance(clock.t + every)


@pytest.mark.parametrize(
    "timeout_s,strikes",
    [(0.1, 3), (0.1, 1), (0.02, 3)],  # last: silence_timeout_s < poll interval
)
async def test_idle_persistent_client_is_not_closed_nor_degraded(make_idle_bridge, monkeypatch, timeout_s, strikes):
    """One turn of audio, then only keepalive for many silence windows (wake-word client)."""
    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.05)
    clock = VClock(monkeypatch)
    bridge, ws = make_idle_bridge(ClientConfig(silence_timeout_s=timeout_s, max_silence_turns=strikes))
    bridge.close_all = AsyncMock()
    loop_task = asyncio.create_task(bridge._client_input_loop())
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await _feed(clock, ws, audio_for=0.03)  # the user speaks a turn (shorter than a tick)...
        bridge.transcriber.last_transcript_at = clock.t  # ...and it is transcribed
        await _feed(clock, ws, keepalive_for=0.8)  # then only keepalive: >= 8 windows, ~16 ticks idle
    finally:
        wd.cancel()
        loop_task.cancel()
    await sent(bridge)
    assert ws.of("status") == [], "degraded notice sent to an idle client"
    bridge.close_all.assert_not_awaited()


async def test_audio_resuming_after_idle_starts_a_fresh_silence_window(make_idle_bridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.05)
    clock = VClock(monkeypatch)
    bridge, ws = make_idle_bridge(ClientConfig(silence_timeout_s=0.2, max_silence_turns=2))
    closed_at: list[float] = []
    bridge.close_all = AsyncMock(side_effect=lambda *a, **k: closed_at.append(clock.t))
    loop_task = asyncio.create_task(bridge._client_input_loop())
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await _feed(clock, ws, audio_for=0.03)  # one turn, transcribed, then idle
        bridge.transcriber.last_transcript_at = clock.t
        await _feed(clock, ws, keepalive_for=0.6)  # idle phase
        await sent(bridge)
        assert not ws.of("status") and not closed_at
        resumed = clock.t
        await _feed(clock, ws, audio_for=1.0)  # wake word again, then silence in the audio
    finally:
        wd.cancel()
        loop_task.cancel()
    await sent(bridge)
    degraded = ws.of("status")
    assert degraded, "continuous audio with no transcription must still be reported"
    assert closed_at, "...and still close the session"
    # the stale pre-idle transcription time must not count: nothing within one window
    assert closed_at[0] - resumed >= 0.2


async def test_watchdog_exits_if_transcriber_degrades_before_any_audio(make_idle_bridge, monkeypatch):
    clock = VClock(monkeypatch)
    bridge, _ = make_idle_bridge()
    wd = asyncio.create_task(bridge._transcription_watchdog())
    await clock.advance(clock.t + 0.5)
    assert not wd.done()  # still waiting for the first audio frame
    bridge.transcriber.state = ConnectionState.DEGRADED
    await clock.advance(clock.t + 0.5)
    assert wd.done() and not wd.cancelled()


async def test_streaming_client_with_no_transcriptions_behaves_as_before(make_idle_bridge, monkeypatch):
    """Regression guard: an always-streaming client (ESP32) gets one strike and one
    degraded notice per poll tick and is closed after max_silence_turns ticks."""
    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.1)
    clock = VClock(monkeypatch)
    bridge, ws = make_idle_bridge(ClientConfig(silence_timeout_s=0.05, max_silence_turns=3))
    bridge.close_all = AsyncMock()
    loop_task = asyncio.create_task(bridge._client_input_loop())
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        for _ in range(300):  # up to 3 virtual seconds of continuous audio
            if wd.done():
                break
            ws.inbox.put_nowait(_audio())
            await clock.advance(clock.t + 0.01)
    finally:
        wd.cancel()
        loop_task.cancel()
    assert wd.done() and not wd.cancelled()
    await sent(bridge)
    assert len(ws.of("status")) == 3
    bridge.close_all.assert_awaited_once()
