"""M6c (#215, #227): client-message validation (G-20) and the silence watchdog
(turn in flight, idle persistent sessions)."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.bridge as bridge_module
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.reconnection import ConnectionState
from tests.fakes.speech import speech_providers


class RecWS:
    def __init__(self):
        self.sent: list = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def send_json(self, p):
        self.sent.append(p)

    async def send_bytes(self, b):
        self.sent.append(b)

    async def receive(self):
        return await self.inbox.get()

    def of(self, type_):
        return [m for m in self.sent if isinstance(m, dict) and m["type"] == type_]


def _bridge(config=None, input_mode="audio"):
    ws = RecWS()
    tracker = MagicMock()
    tracker.record = AsyncMock()
    tracker.close = AsyncMock()
    b = JotaBridge(
        client=Client(id="u", client_key="k", is_active=True),
        config=config or ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(),
        tracker=tracker,
        handshake=Handshake(client_key="k", input_mode=input_mode, output_mode=["text"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    if input_mode == "audio":
        t = MagicMock()
        t.state = ConnectionState.CONNECTED
        t.last_transcript_at = None
        t.send_audio = AsyncMock()
        t.send_end = AsyncMock()
        b.transcriber = t
    else:
        b.transcriber = None
    return b, ws


def _text(obj_or_str):
    s = obj_or_str if isinstance(obj_or_str, str) else json.dumps(obj_or_str)
    return {"type": "websocket.receive", "text": s}


# ── G-20 ─────────────────────────────────────────────────────────────────────


async def test_malformed_client_messages_get_a_non_fatal_error_and_the_session_goes_on():
    bridge, ws = _bridge()
    bad = ["42", "[1]", '"s"', "true", "null", '{"type":"send","text":5}', '{"type":"send","text":null}']
    for raw in bad:
        ws.inbox.put_nowait(_text(raw))
    ws.inbox.put_nowait(_text({"type": "end"}))
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await asyncio.wait_for(bridge._client_input_loop(), 2.0)

    errors = ws.of("error")
    assert len(errors) == len(bad)
    assert all(e["fatal"] is False and e["code"] == "INVALID_MESSAGE" for e in errors)
    assert "secret" not in json.dumps(errors)
    bridge.transcriber.send_end.assert_awaited_once()  # the loop survived all of them


async def test_non_string_send_text_never_reaches_the_orchestrator():
    bridge, ws = _bridge(input_mode="text")
    ws.inbox.put_nowait(_text({"type": "send", "text": ["x"]}))
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge._client_input_loop(), 2.0)
    assert bridge._active_turn is None
    assert len(ws.of("error")) == 1


async def test_keepalive_and_unknown_dict_messages_stay_silent():
    bridge, ws = _bridge()
    ws.inbox.put_nowait(_text({"type": "keepalive"}))
    ws.inbox.put_nowait(_text({}))
    ws.inbox.put_nowait(_text({"type": "send"}))  # no text: ignored as before
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge._client_input_loop(), 2.0)
    assert ws.of("error") == []


# ── watchdog: turn in flight (decision of Sito) ──────────────────────────────


class FakeClock:
    def __init__(self):
        self.t = 1000.0


async def test_watchdog_does_not_close_during_a_20s_turn_with_2s_timeout_3_strikes(monkeypatch):
    """Literal acceptance test: audio session, 20 s turn, silence_timeout_s=2,
    max_silence_turns=3 -> not closed. Virtual clock; audio keeps flowing so the
    only thing protecting the session is the in-flight turn."""
    real_sleep = asyncio.sleep
    clock = FakeClock()

    async def fake_sleep(d):
        clock.t += d
        await real_sleep(0)

    monkeypatch.setattr(bridge_module, "time", SimpleNamespace(monotonic=lambda: clock.t))
    monkeypatch.setattr(bridge_module.asyncio, "sleep", fake_sleep)
    bridge, _ = _bridge(ClientConfig(silence_timeout_s=2, max_silence_turns=3))
    start = clock.t
    bridge._last_audio_at = bridge._audio_burst_start = start
    bridge.transcriber.last_transcript_at = start
    events: list[tuple[str, float]] = []
    bridge.close_all = AsyncMock(side_effect=lambda *a, **k: events.append(("close", clock.t)))
    real_notify = bridge.notify_service_status

    async def spy(service, state, *a, **k):
        events.append((state, clock.t))
        await real_notify(service, state, *a, **k)

    bridge.notify_service_status = spy

    async def turn():
        while clock.t < start + 20:
            await real_sleep(0)

    bridge._active_turn = asyncio.create_task(turn())

    async def streamer():
        while True:
            bridge._last_audio_at = clock.t
            await real_sleep(0)

    s = asyncio.create_task(streamer())
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await asyncio.wait_for(_until(lambda: any(e[0] == "close" for e in events)), 5.0)
    finally:
        s.cancel()
        wd.cancel()
    first = min(ts for _, ts in events)
    assert first >= start + 20, f"silence strike/close at +{first - start}s, during the turn"
    # the first window after the turn is a fresh one: no strike on the first tick after it ends
    assert min(ts for st, ts in events if st == "degraded") >= start + 22
    # ...and silence after the turn still closes the session
    assert any(e[0] == "close" for e in events)


_REAL_SLEEP = asyncio.sleep  # captured at import, before any test patches it


async def _until(pred):
    while not pred():
        await _REAL_SLEEP(0)


# ── #227: idle persistent sessions ───────────────────────────────────────────


async def _feed(ws, *, audio_for=0.0, keepalive_for=0.0, every=0.01):
    end = time.monotonic() + audio_for
    while time.monotonic() < end:
        ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x00\x00\x00\x00"})
        await asyncio.sleep(every)
    end = time.monotonic() + keepalive_for
    while time.monotonic() < end:
        ws.inbox.put_nowait(_text({"type": "keepalive"}))
        await asyncio.sleep(every)


@pytest.mark.parametrize(
    "timeout_s,strikes",
    [(0.1, 3), (0.1, 1), (0.02, 3)],  # last: silence_timeout_s < poll interval
)
async def test_idle_persistent_client_is_not_closed_nor_degraded(monkeypatch, timeout_s, strikes):
    """One turn of audio, then only keepalive for many silence windows (wake-word client)."""
    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.05)
    bridge, ws = _bridge(ClientConfig(silence_timeout_s=timeout_s, max_silence_turns=strikes))
    bridge.close_all = AsyncMock()
    loop_task = asyncio.create_task(bridge._client_input_loop())
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await _feed(ws, audio_for=0.03)  # the user speaks a turn (shorter than a tick)...
        bridge.transcriber.last_transcript_at = time.monotonic()  # ...and it is transcribed
        await _feed(ws, keepalive_for=0.8)  # then only keepalive: >= 8 windows, ~16 ticks idle
    finally:
        wd.cancel()
        loop_task.cancel()
    assert ws.of("status") == [], "degraded notice sent to an idle client"
    bridge.close_all.assert_not_awaited()


async def test_audio_resuming_after_idle_starts_a_fresh_silence_window(monkeypatch):
    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.05)
    bridge, ws = _bridge(ClientConfig(silence_timeout_s=0.2, max_silence_turns=2))
    closed_at: list[float] = []
    bridge.close_all = AsyncMock(side_effect=lambda *a, **k: closed_at.append(time.monotonic()))
    loop_task = asyncio.create_task(bridge._client_input_loop())
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await _feed(ws, audio_for=0.03)  # one turn, transcribed, then idle
        bridge.transcriber.last_transcript_at = time.monotonic()
        await _feed(ws, keepalive_for=0.6)  # idle phase
        assert not ws.of("status") and not closed_at
        resumed = time.monotonic()
        await _feed(ws, audio_for=1.0)  # wake word again, then silence in the audio
    finally:
        wd.cancel()
        loop_task.cancel()
    degraded = ws.of("status")
    assert degraded, "continuous audio with no transcription must still be reported"
    assert closed_at, "...and still close the session"
    # the stale pre-idle transcription time must not count: nothing within one window
    assert closed_at[0] - resumed >= 0.2


async def test_streaming_client_with_no_transcriptions_behaves_as_before(monkeypatch):
    """Regression guard: an always-streaming client (ESP32) gets one strike and one
    degraded notice per poll tick and is closed after max_silence_turns ticks."""
    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.1)
    bridge, ws = _bridge(ClientConfig(silence_timeout_s=0.05, max_silence_turns=3))
    bridge.close_all = AsyncMock()
    loop_task = asyncio.create_task(bridge._client_input_loop())
    feeder = asyncio.create_task(_feed(ws, audio_for=3.0))
    wd = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await asyncio.wait_for(wd, timeout=3.0)
    finally:
        feeder.cancel()
        loop_task.cancel()
    assert len(ws.of("status")) == 3
    bridge.close_all.assert_awaited_once()
