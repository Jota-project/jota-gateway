"""Tests for JotaBridge._transcription_watchdog."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.pipeline_tracker import PipelineTracker, _NullWS

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)
_CONFIG = ClientConfig(silence_timeout_s=1, max_silence_turns=3)


def _make_bridge(config=None):
    ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:wd",
        client_id="hab_sito",
        input_mode="audio",
        output_mode=["text"],
        client_ws=_NullWS(),
        registry=registry,
    )
    handshake = Handshake(client_key="test-key", input_mode="audio", output_mode=["text"])
    orch = AsyncMock()
    transcriber = MagicMock()
    from src.services.reconnection import ConnectionState

    transcriber.state = ConnectionState.CONNECTED
    transcriber._last_transcription_at = None
    bridge = JotaBridge(
        client=_CLIENT,
        config=config or _CONFIG,
        client_ws=ws,
        orchestrator=orch,
        tts=AsyncMock(),
        tracker=tracker,
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge.transcriber = transcriber
    return bridge, ws, transcriber


@pytest.mark.asyncio
async def test_watchdog_notifies_client_after_silence_timeout():
    """After silence_timeout_s with no transcription, client gets degraded notice."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=1)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = None
    bridge.close_all = AsyncMock()

    with patch("src.services.bridge.asyncio.sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "status"
    assert payload["service"] == "transcriber"
    assert payload["state"] == "degraded"


@pytest.mark.asyncio
async def test_watchdog_exits_if_transcriber_disconnects():
    """Watchdog exits cleanly when transcriber goes offline."""
    bridge, ws, transcriber = _make_bridge()
    bridge._first_audio_at = time.monotonic()
    from src.services.reconnection import ConnectionState

    transcriber.state = ConnectionState.DEGRADED

    with patch("src.services.bridge.asyncio.sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)


@pytest.mark.asyncio
async def test_watchdog_closes_session_after_max_silence_turns():
    """After max_silence_turns consecutive timeouts, close_all is called."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = None

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    with patch("src.services.bridge.asyncio.sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    assert close_called, "close_all debería haberse llamado tras max_silence_turns"
    assert ws.send_json.call_count == 2  # una notificación por cada silencio


@pytest.mark.asyncio
async def test_watchdog_resets_count_when_transcription_arrives():
    """Un silencio seguido de transcripción reinicia el contador."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = None

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        # Tras el primer ciclo (1 silencio), simular que llega una transcripción
        if ticks["n"] == 2:
            transcriber._last_transcription_at = time.monotonic()
        elif ticks["n"] == 4:
            from src.services.reconnection import ConnectionState

            transcriber.state = ConnectionState.DEGRADED  # stop the loop cleanly

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        # NOTE: must directly `await` the watchdog coroutine, not wrap it in
        # `asyncio.create_task` + an outer polling loop — patching
        # `asyncio.sleep` globally means a non-suspending mock never actually
        # yields control back to the scheduler, so a separately-created task
        # never gets to run at all (verified: deliberately breaking the
        # watchdog's RECONNECTING handling didn't fail the old version of this
        # test). Direct await drives the loop for real.
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    assert not close_called, "No debería haber cerrado — el contador se reinició"


@pytest.mark.asyncio
async def test_watchdog_pauses_but_does_not_exit_when_reconnecting():
    """A transient RECONNECTING must not permanently kill the watchdog (the bug
    this task fixes: the old `if not _is_ready: return` treated any drop as final)."""
    from src.services.reconnection import ConnectionState

    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = time.monotonic()  # fresh, no silence yet

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] == 1:
            transcriber.state = ConnectionState.RECONNECTING  # transient drop
        elif ticks["n"] == 2:
            transcriber.state = ConnectionState.CONNECTED  # recovered
            transcriber._last_transcription_at = time.monotonic()
        elif ticks["n"] == 4:
            transcriber.state = ConnectionState.DEGRADED  # stop the loop cleanly

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        # See NOTE above test_watchdog_resets_count_when_transcription_arrives —
        # direct await, not create_task + outer polling loop.
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    assert ticks["n"] >= 4, "watchdog must still be ticking after the RECONNECTING blip"
    assert not close_called


@pytest.mark.asyncio
async def test_watchdog_does_not_close_immediately_after_reconnect_with_stale_timestamp():
    """Issue #149: TranscriberClient.connect() never resets _last_transcription_at,
    so after a real reconnect it stays at whatever it was before the outage. The
    watchdog must not blame the client for the outage duration and force-close the
    session the moment it observes CONNECTED again — it should grant a fresh
    baseline instead, exactly like a brand-new session would get via
    `_first_audio_at`."""
    from src.services.reconnection import ConnectionState

    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 10
    # A real transcription happened a while ago, then the transcriber dropped.
    transcriber._last_transcription_at = time.monotonic() - 10

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] == 1:
            transcriber.state = ConnectionState.RECONNECTING  # transient drop
        elif ticks["n"] == 2:
            transcriber.state = ConnectionState.CONNECTED  # recovered
            # Deliberately NOT touching _last_transcription_at here — production's
            # TranscriberClient.connect() doesn't reset it either.
        elif ticks["n"] == 4:
            transcriber.state = ConnectionState.DEGRADED  # stop the loop cleanly

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        # See NOTE above test_watchdog_resets_count_when_transcription_arrives —
        # direct await, not create_task + outer polling loop.
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)

    assert not close_called, (
        "session was force-closed right after a successful reconnect, "
        "because elapsed was measured from a pre-outage timestamp"
    )


@pytest.mark.asyncio
async def test_idle_watchdog_closes_session_when_no_activity(monkeypatch):
    """#115: sin ningún mensaje del cliente durante IDLE_TIMEOUT_S, la
    sesión se cierra."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge, ws, _ = _make_bridge()
    bridge._last_client_activity = time.monotonic()

    close_called = []

    async def _fake_close():
        close_called.append(True)

    bridge.close_all = _fake_close

    await asyncio.wait_for(bridge._idle_watchdog(), timeout=1.0)

    assert close_called


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_close_while_activity_is_recent(monkeypatch):
    """Caso de control: con IDLE_TIMEOUT_S holgado y actividad reciente, el
    watchdog sigue durmiendo — no cierra la sesión."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 5)
    bridge, ws, _ = _make_bridge()
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
async def test_idle_watchdog_does_not_close_during_active_turn(monkeypatch):
    """#115 follow-up (real production bug): a text session with the
    orchestrator actively streaming tokens must not be cut off mid-turn just
    because IDLE_TIMEOUT_S has elapsed since the client's last inbound
    message — `_last_client_activity` only reflects the client's last
    *message*, not whether the server is still actively responding."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge, ws, _ = _make_bridge()
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

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        with pytest.raises(RuntimeError, match="stop-test"):
            await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)

    # The watchdog looped several times (re-checking every ~2s per the fix)
    # without ever calling close_all() while the turn stayed active.
    assert ticks["n"] >= 3
    assert not close_called, "watchdog must not close while a turn is active"


@pytest.mark.asyncio
async def test_idle_watchdog_closes_once_active_turn_finishes_and_idle_elapses(monkeypatch):
    """Control case for the fix above: once the in-flight turn completes
    (task done) and the client is still genuinely idle, the watchdog must
    still close the session on its next re-check — the active-turn gate only
    defers the close while something is in flight, it doesn't disable idle
    timeout permanently."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge, ws, _ = _make_bridge()
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

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        await asyncio.wait_for(bridge._idle_watchdog(), timeout=2.0)

    assert close_called, "watchdog must close once the turn finished and idle time elapsed"


@pytest.mark.asyncio
async def test_run_launches_idle_watchdog_that_closes_the_session(monkeypatch, mock_tracker):
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

    from src.core.config import settings

    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)

    ws = AsyncMock()
    hang = asyncio.Event()

    async def _hanging_receive():
        await hang.wait()  # never set — mimics a client that never sends anything

    ws.receive = _hanging_receive

    bridge = JotaBridge(
        client=_CLIENT,
        config=ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )

    start = time_module.monotonic()
    # Generous outer safety net — NOT the mechanism under test. If the idle
    # watchdog isn't wired in, this will still eventually "complete" via
    # wait_for's own cancellation being absorbed by run()'s cleanup, but only
    # after ~2s — the elapsed assertion below is what actually catches that.
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    elapsed = time_module.monotonic() - start

    assert bridge._closed is True
    assert elapsed < 1.0, (
        f"run() took {elapsed:.2f}s — the idle watchdog likely wasn't launched "
        "and this only completed via the outer wait_for's own timeout"
    )
