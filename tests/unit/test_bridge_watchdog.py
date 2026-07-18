"""Tests for JotaBridge._transcription_watchdog."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.pipeline_tracker import PipelineTracker, _NullWS

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)
_CONFIG = ClientConfig(silence_timeout_s=1, max_silence_turns=3)


def _make_bridge(config=None):
    ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:wd", client_id="hab_sito",
        input_mode="audio", output_mode=["text"],
        client_ws=_NullWS(), registry=registry,
    )
    handshake = Handshake(client_key="test-key", input_mode="audio", output_mode=["text"])
    orch = AsyncMock()
    transcriber = MagicMock()
    from src.services.reconnection import ConnectionState
    transcriber.state = ConnectionState.CONNECTED
    transcriber._last_transcription_at = None
    bridge = JotaBridge(client=_CLIENT, config=config or _CONFIG, client_ws=ws,
                        orchestrator=orch, tts=AsyncMock(), tracker=tracker, handshake=handshake,
                        client_registry=ClientRegistry(), default_agent="main")
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
