"""Tests for FogarBridge._transcription_watchdog."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import fogar_gateway.services.bridge as bridge_module
from fogar_gateway.models.schemas import ClientConfig
from fogar_gateway.services.pipeline_tracker import PipelineTracker
from fogar_gateway.services.reconnection import ConnectionState
from tests.fakes.bridge import sent

_CONFIG = ClientConfig(silence_timeout_s=1, max_silence_turns=3)


@pytest.fixture
def make_wd_bridge(make_bridge):
    def _make(config=None):
        tracker = PipelineTracker(
            session_id="test:wd",
            client_id="hab_sito",
            input_mode="audio",
            output_mode=["text"],
            registry=MagicMock(),
        )
        transcriber = MagicMock()
        transcriber.state = ConnectionState.CONNECTED
        transcriber.last_transcript_at = None
        bridge, ws = make_bridge(
            client_id="hab_sito",
            config=config or _CONFIG,
            input_mode="audio",
            tracker=tracker,
            transcriber=transcriber,
        )
        return bridge, ws, transcriber

    return _make


@pytest.mark.asyncio
async def test_watchdog_notifies_client_after_silence_timeout(make_wd_bridge):
    """After silence_timeout_s with no transcription, client gets degraded notice."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=1)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic() - 2
    bridge._last_audio_at = float("inf")  # client streaming audio without gaps (#227)
    transcriber.last_transcript_at = None
    bridge.close_all = AsyncMock()

    with patch("fogar_gateway.services.bridge._sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    msgs = await sent(bridge)
    assert len(msgs) == 1
    payload = msgs[0]
    assert payload["type"] == "status"
    assert payload["service"] == "transcriber"
    assert payload["state"] == "degraded"


@pytest.mark.asyncio
async def test_watchdog_exits_if_transcriber_disconnects(make_wd_bridge):
    """Watchdog exits cleanly when transcriber goes offline."""
    bridge, ws, transcriber = make_wd_bridge()
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic()
    transcriber.state = ConnectionState.DEGRADED

    with patch("fogar_gateway.services.bridge._sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)


@pytest.mark.asyncio
async def test_watchdog_closes_session_after_max_silence_turns(make_wd_bridge):
    """After max_silence_turns consecutive timeouts, close_all is called."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic() - 2
    bridge._last_audio_at = float("inf")  # client streaming audio without gaps (#227)
    transcriber.last_transcript_at = None

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    with patch("fogar_gateway.services.bridge._sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    assert close_called, "close_all debería haberse llamado tras max_silence_turns"
    assert len(await sent(bridge)) == 2  # una notificación por cada silencio


@pytest.mark.asyncio
async def test_watchdog_resets_count_when_transcription_arrives(make_wd_bridge):
    """Un silencio seguido de transcripción reinicia el contador."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic() - 2
    bridge._last_audio_at = float("inf")  # client streaming audio without gaps (#227)
    transcriber.last_transcript_at = None

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        # Tras el primer ciclo (1 silencio), simular que llega una transcripción
        if ticks["n"] == 2:
            transcriber.last_transcript_at = time.monotonic()
        elif ticks["n"] == 4:
            transcriber.state = ConnectionState.DEGRADED  # stop the loop cleanly

    with patch("fogar_gateway.services.bridge._sleep", new=_controlled_sleep):
        # NOTE: must directly `await` the watchdog coroutine, not wrap it in
        # `asyncio.create_task` + an outer polling loop — a non-suspending
        # `_sleep` never yields control back to the scheduler, so a separately-created task
        # never gets to run at all (verified: deliberately breaking the
        # watchdog's RECONNECTING handling didn't fail the old version of this
        # test). Direct await drives the loop for real.
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    assert not close_called, "No debería haber cerrado — el contador se reinició"


@pytest.mark.asyncio
async def test_watchdog_pauses_but_does_not_exit_when_reconnecting(make_wd_bridge):
    """A transient RECONNECTING must not permanently kill the watchdog (the bug
    this task fixes: the old `if not _is_ready: return` treated any drop as final)."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic() - 2
    bridge._last_audio_at = float("inf")  # client streaming audio without gaps (#227)
    transcriber.last_transcript_at = time.monotonic()  # fresh, no silence yet

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] == 1:
            transcriber.state = ConnectionState.RECONNECTING  # transient drop
        elif ticks["n"] == 2:
            transcriber.state = ConnectionState.CONNECTED  # recovered
            transcriber.last_transcript_at = time.monotonic()
        elif ticks["n"] == 4:
            transcriber.state = ConnectionState.DEGRADED  # stop the loop cleanly

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    with patch("fogar_gateway.services.bridge._sleep", new=_controlled_sleep):
        # See NOTE above test_watchdog_resets_count_when_transcription_arrives —
        # direct await, not create_task + outer polling loop.
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    assert ticks["n"] >= 4, "watchdog must still be ticking after the RECONNECTING blip"
    assert not close_called


@pytest.mark.asyncio
async def test_watchdog_does_not_close_immediately_after_reconnect_with_stale_timestamp(make_wd_bridge):
    """Issue #149: TranscriberClient.connect() never resets last_transcript_at,
    so after a real reconnect it stays at whatever it was before the outage. The
    watchdog must not blame the client for the outage duration and force-close the
    session the moment it observes CONNECTED again — it should grant a fresh
    baseline instead, exactly like a brand-new session would get on its first
    audio frame."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic() - 10
    bridge._last_audio_at = float("inf")  # client streaming audio without gaps (#227)
    # A real transcription happened a while ago, then the transcriber dropped.
    transcriber.last_transcript_at = time.monotonic() - 10

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] == 1:
            transcriber.state = ConnectionState.RECONNECTING  # transient drop
        elif ticks["n"] == 2:
            transcriber.state = ConnectionState.CONNECTED  # recovered
            # Deliberately NOT touching last_transcript_at here — production's
            # TranscriberClient.connect() doesn't reset it either.
        elif ticks["n"] == 4:
            transcriber.state = ConnectionState.DEGRADED  # stop the loop cleanly

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    with patch("fogar_gateway.services.bridge._sleep", new=_controlled_sleep):
        # See NOTE above test_watchdog_resets_count_when_transcription_arrives —
        # direct await, not create_task + outer polling loop.
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    assert not close_called, (
        "session was force-closed right after a successful reconnect, "
        "because elapsed was measured from a pre-outage timestamp"
    )


@pytest.mark.asyncio
async def test_idle_watchdog_closes_session_when_no_activity(make_wd_bridge, monkeypatch):
    """#115: sin ningún mensaje del cliente durante IDLE_TIMEOUT_S, la
    sesión se cierra."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic()

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    await asyncio.wait_for(bridge._idle_watchdog(), timeout=1.0)

    assert close_called


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_close_while_activity_is_recent(make_wd_bridge, monkeypatch):
    """Caso de control: con IDLE_TIMEOUT_S holgado y actividad reciente, el
    watchdog sigue durmiendo — no cierra la sesión."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 5)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic()

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    task = asyncio.create_task(bridge._idle_watchdog())
    await asyncio.sleep(0.05)
    assert not task.done()
    assert not close_called

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_close_during_active_turn(make_wd_bridge, monkeypatch):
    """#115 follow-up (real production bug): a text session with the
    orchestrator actively streaming tokens must not be cut off mid-turn just
    because IDLE_TIMEOUT_S has elapsed since the client's last inbound
    message — `_last_client_activity` only reflects the client's last
    *message*, not whether the server is still actively responding."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10  # idle-timeout would fire

    active_turn = MagicMock()
    active_turn.done.return_value = False
    bridge._active_turn = active_turn

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise RuntimeError("stop-test")  # bounded termination, see below

    with patch("fogar_gateway.services.bridge._sleep", new=_controlled_sleep):
        with pytest.raises(RuntimeError, match="stop-test"):
            await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)

    # The watchdog looped several times (re-checking every ~2s per the fix)
    # without ever calling close_all() while the turn stayed active.
    assert ticks["n"] >= 3
    assert not close_called, "watchdog must not close while a turn is active"


@pytest.mark.asyncio
async def test_idle_watchdog_closes_once_active_turn_finishes_and_idle_elapses(make_wd_bridge, monkeypatch):
    """Control case for the fix above: once the in-flight turn completes
    (task done) and the client is still genuinely idle, the watchdog must
    still close the session on its next re-check — the active-turn gate only
    defers the close while something is in flight, it doesn't disable idle
    timeout permanently."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10  # stale from the start

    active_turn = MagicMock()
    active_turn.done.return_value = False
    bridge._active_turn = active_turn

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] == 1:
            assert not close_called, "must not close on the first tick — turn still active"
            active_turn.done.return_value = True  # turn finishes between checks

    with patch("fogar_gateway.services.bridge._sleep", new=_controlled_sleep):
        await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)

    assert close_called, "watchdog must close once the turn finished and idle time elapsed"


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_close_while_push_within_grace_period(make_wd_bridge, monkeypatch):
    """An orphaned push (agent-start with no matching agent-end, e.g. OpenClaw
    crashed mid-push) must still defer the idle close while it's within its
    own TURN_TIMEOUT_S grace period — same behavior as an active turn."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 5)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10  # idle-timeout would fire

    bridge._push_turn_open = True
    bridge._push_turn_opened_at = time.monotonic()  # just opened

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    task = asyncio.create_task(bridge._idle_watchdog())
    await asyncio.sleep(0.1)
    assert not task.done()
    assert not close_called, "watchdog must not close while push is within its grace period"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_idle_watchdog_closes_when_push_exceeds_grace_period(make_wd_bridge, monkeypatch):
    """Regression test for the orphaned-push leak: a push turn left open
    (agent-start with no matching agent-end) past TURN_TIMEOUT_S must no
    longer block the idle watchdog from calling close_all() — without the
    fix, `_push_turn_open` alone gates the close forever and this test
    fails because close_all() is never called."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.05)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10  # idle-timeout would fire

    bridge._push_turn_open = True
    bridge._push_turn_opened_at = time.monotonic() - 5  # opened long ago, past grace period

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)

    assert close_called, "watchdog must close once the orphaned push exceeds TURN_TIMEOUT_S"


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_close_while_push_backlog_within_grace(make_wd_bridge, monkeypatch):
    """#130 S3a: a push event queued but not yet processed is activity in flight."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 5)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10

    bridge._push_pending = 1
    bridge._push_pending_since = time.monotonic()

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    task = asyncio.create_task(bridge._idle_watchdog())
    await asyncio.sleep(0.1)
    assert not task.done()
    assert not close_called

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_idle_watchdog_closes_when_push_backlog_exceeds_grace(make_wd_bridge, monkeypatch):
    """Review focus 5: a wedged push worker + silent client must not leak the
    session — the backlog only defers the idle close for TURN_TIMEOUT_S."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.05)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10

    bridge._push_pending = 1
    bridge._push_pending_since = time.monotonic() - 5

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)

    assert close_called


@pytest.mark.asyncio
async def test_run_launches_idle_watchdog_that_closes_the_session(
    make_bridge, monkeypatch, mock_tracker
):
    """Drives the REAL bridge.run() (not `_idle_watchdog()` directly) end to
    end, so a regression that deletes
    `self.tasks.append(asyncio.create_task(self._idle_watchdog()))` from
    run() fails this test instead of passing silently — the deleted line
    left the rest of the suite green because nothing else exercises run()'s
    task-launching code for the idle watchdog specifically.

    client_ws.receive() hangs forever (client never sends anything, e.g. a
    push-only session), so _client_input_loop never finishes on its own —
    only the idle watchdog closing the session can make run() return before
    the outer wait_for's own timeout.

    Timing, not just eventual completion, is what discriminates fixed vs.
    unfixed here: run()'s own try/except/finally swallows CancelledError and
    always calls close_all() during cleanup, so if the idle watchdog were
    never launched, the outer wait_for's cancellation at its own timeout
    would *also* end up with `_closed is True` — just ~40x slower. The
    elapsed-time assertion is what proves it was IDLE_TIMEOUT_S=0.05s that
    closed the session, not the outer safety net.
    """
    import time as time_module

    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)

    # RecWS.receive() waits forever: a client that never sends anything.
    bridge, _ = make_bridge(client_id="hab_sito", tracker=mock_tracker)

    start = time_module.monotonic()
    # Generous outer safety net — NOT the mechanism under test. If the idle
    # watchdog isn't wired in, wait_for cancels run() after ~2s and this raises
    # TimeoutError (run() no longer swallows the cancellation, #221).
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    elapsed = time_module.monotonic() - start

    assert bridge._closed is True
    assert elapsed < 1.0, (
        f"run() took {elapsed:.2f}s — the idle watchdog likely wasn't launched "
        "and this only completed via the outer wait_for's own timeout"
    )


@pytest.mark.asyncio
async def test_watchdog_counts_one_strike_per_poll_tick_not_per_timeout_window(make_wd_bridge, monkeypatch):
    """#129: pins the documented semantics with REAL time (sleep is not patched).

    Each poll tick with elapsed > silence_timeout_s is one strike, and each strike
    sends one `degraded` notice — max_silence_turns counts ticks, not full
    silence_timeout_s windows. Here the timeout (0.05s) is shorter than the tick
    (0.1s), so a "window" model would still wait 3 × 0.05s; the tick model closes
    after exactly 3 ticks.
    """

    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.1)
    config = ClientConfig(silence_timeout_s=0.05, max_silence_turns=3)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._last_audio_at = bridge._audio_burst_start = time.monotonic() - 1
    bridge._last_audio_at = float("inf")  # client streaming audio without gaps (#227)
    transcriber.last_transcript_at = None
    bridge.close_all = AsyncMock()

    await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    await sent(bridge)
    degraded = [m for m in ws.of("status") if m["state"] == "degraded"]
    assert len(degraded) == 3
    bridge.close_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchdog_extends_first_window_after_burst_by_stt_warmup(make_wd_bridge, monkeypatch):
    """Un STT con arranque lento (warmup_s) no recibe strikes en la primera ventana tras abrir
    ráfaga: solo se cuenta silencio pasado silence_timeout_s + warmup_s. Tiempo real."""
    from fogar_gateway.services.speech.ports import SttCapabilities

    monkeypatch.setattr(bridge_module, "_WATCHDOG_POLL_S", 0.05)
    config = ClientConfig(silence_timeout_s=0.05, max_silence_turns=1)
    bridge, ws, transcriber = make_wd_bridge(config=config)
    bridge._stt = SimpleNamespace(capabilities=SttCapabilities(partials=True, warmup_s=0.5))
    t0 = time.monotonic()
    bridge._last_audio_at = bridge._audio_burst_start = t0
    bridge._audio_burst_start = t0
    bridge._last_audio_at = float("inf")
    transcriber.last_transcript_at = None
    bridge.close_all = AsyncMock()

    task = asyncio.create_task(bridge._transcription_watchdog())
    try:
        await asyncio.sleep(0.35)  # dentro de timeout + warmup (0.55 s)
        await sent(bridge)
        assert [m for m in ws.of("status") if m["state"] == "degraded"] == []
        await asyncio.wait_for(task, timeout=2.0)  # pasada la ventana, los strikes vuelven
    finally:
        task.cancel()
    assert time.monotonic() - t0 >= 0.55
    bridge.close_all.assert_awaited_once()


# ── turno en vuelo (#215, decisión de Sito), reloj virtual ──────────────────


class FakeClock:
    def __init__(self):
        self.t = 1000.0


async def test_watchdog_does_not_close_during_a_20s_turn_with_2s_timeout_3_strikes(
    make_wd_bridge, monkeypatch
):
    """Literal acceptance test: audio session, 20 s turn, silence_timeout_s=2,
    max_silence_turns=3 -> not closed. Virtual clock; audio keeps flowing so the
    only thing protecting the session is the in-flight turn."""
    clock = FakeClock()

    async def fake_sleep(d):
        clock.t += d
        await asyncio.sleep(0)

    monkeypatch.setattr(bridge_module, "time", SimpleNamespace(monotonic=lambda: clock.t))
    monkeypatch.setattr(bridge_module, "_sleep", fake_sleep)
    bridge, _, _ = make_wd_bridge(ClientConfig(silence_timeout_s=2, max_silence_turns=3))
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
            await asyncio.sleep(0)

    bridge._active_turn = asyncio.create_task(turn())

    async def streamer():
        while True:
            bridge._last_audio_at = clock.t
            await asyncio.sleep(0)

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


async def _until(pred):
    while not pred():
        await asyncio.sleep(0)


async def test_push_with_audio_playing_counts_as_in_flight(make_wd_bridge, monkeypatch):
    """Con pacing el audio de un push suena más allá de TURN_TIMEOUT_S: mientras su
    tarea de audio viva, el push cuenta como turno en curso."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.1)
    bridge, _, _ = make_wd_bridge()
    bridge._push_turn_open = True
    bridge._push_turn_opened_at = time.monotonic() - 1  # abierto hace más de TURN_TIMEOUT_S

    bridge._push_audio_task = asyncio.create_task(asyncio.sleep(3600))
    try:
        bridge._push_audio_progress_at = time.monotonic()
        assert bridge._turn_in_flight() is True
        # orphaned push: a live audio task with no recent progress no longer counts
        bridge._push_audio_progress_at = time.monotonic() - settings.PUSH_TTS_DRAIN_TIMEOUT_S - 1
        assert bridge._turn_in_flight() is False
    finally:
        bridge._push_audio_task.cancel()
        await asyncio.wait({bridge._push_audio_task})

    assert bridge._turn_in_flight() is False


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_close_while_push_audio_progresses(make_wd_bridge, monkeypatch):
    """7e: a paced push sounds past TURN_TIMEOUT_S; while its audio progresses it is in flight."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.05)
    bridge, ws, _ = make_wd_bridge()
    bridge._last_client_activity = time.monotonic() - 10
    bridge._push_turn_open = True
    bridge._push_turn_opened_at = time.monotonic() - 5  # past TURN_TIMEOUT_S
    audio = asyncio.create_task(asyncio.sleep(3600))
    bridge._push_audio_task = audio
    bridge._push_audio_progress_at = time.monotonic()

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close
    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        bridge._push_audio_progress_at = time.monotonic()  # audio keeps flowing
        if ticks["n"] >= 3:
            raise RuntimeError("stop-test")

    try:
        with patch("fogar_gateway.services.bridge._sleep", new=_controlled_sleep):
            with pytest.raises(RuntimeError, match="stop-test"):
                await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)
    finally:
        audio.cancel()
    assert not close_called
