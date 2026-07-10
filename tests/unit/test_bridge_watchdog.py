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
                        orchestrator=orch, tracker=tracker, handshake=handshake,
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
    bridge._close_all = AsyncMock()

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
    """After max_silence_turns consecutive timeouts, _close_all is called."""
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = None

    close_called = []
    async def _fake_close():
        close_called.append(True)

    bridge._close_all = _fake_close

    with patch("src.services.bridge.asyncio.sleep", new=AsyncMock(return_value=None)):
        await asyncio.wait_for(bridge._transcription_watchdog(), timeout=3.0)

    assert close_called, "_close_all debería haberse llamado tras max_silence_turns"
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

    bridge._close_all = _fake_close

    call_count = 0

    async def _controlled_sleep(n):
        nonlocal call_count
        call_count += 1
        # Tras el primer ciclo (1 silencio), simular que llega una transcripción
        if call_count == 2:
            transcriber._last_transcription_at = time.monotonic()

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        # Con max_silence_turns=2, si el conteo se resetea debería necesitar 2 ciclos más
        # Dejamos correr suficiente para que si no se resetea ya habría cerrado
        task = asyncio.create_task(bridge._transcription_watchdog())
        for _ in range(6):
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert not close_called, "No debería haber cerrado — el contador se reinició"


@pytest.mark.asyncio
async def test_watchdog_pauses_but_does_not_exit_when_reconnecting():
    """A transient RECONNECTING must not permanently kill the watchdog (the bug
    this task fixes: the old `if not _is_ready: return` treated any drop as final)."""
    from src.services.reconnection import ConnectionState
    config = ClientConfig(silence_timeout_s=1, max_silence_turns=2)
    bridge, ws, transcriber = _make_bridge(config=config)
    bridge._first_audio_at = time.monotonic() - 2
    transcriber._last_transcription_at = None

    ticks = {"n": 0}

    async def _controlled_sleep(n):
        ticks["n"] += 1
        if ticks["n"] == 1:
            transcriber.state = ConnectionState.RECONNECTING  # transient drop
        elif ticks["n"] == 2:
            transcriber.state = ConnectionState.CONNECTED  # recovered
            transcriber._last_transcription_at = time.monotonic()

    close_called = []
    bridge._close_all = lambda: close_called.append(True)

    with patch("src.services.bridge.asyncio.sleep", new=_controlled_sleep):
        task = asyncio.create_task(bridge._transcription_watchdog())
        for _ in range(6):
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert ticks["n"] >= 3, "watchdog must still be ticking after the RECONNECTING blip"
    assert not close_called
