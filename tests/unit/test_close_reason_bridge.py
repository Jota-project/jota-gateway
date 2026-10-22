"""Motivo de cierre de la sesión en session_end y en gateway_stats (#219, 6g)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from src.core.config import settings
from src.core.exceptions import ClientInactive
from src.core.session_key import make_session_key
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.pipeline_tracker import PipelineTracker
from src.services.reconnection import ConnectionState
from src.services.stats import gateway_stats

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)


def _make_bridge(config=None, input_mode="text", turn_registry=None):
    tracker = PipelineTracker(
        session_id="test:reason",
        client_id=_CLIENT.id,
        input_mode=input_mode,
        output_mode=["text"],
        client_ws=AsyncMock(),
        registry=MagicMock(),
    )
    registry = ClientRegistry()
    bridge = JotaBridge(
        client=_CLIENT,
        config=config or ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=tracker,
        handshake=Handshake(client_key="test-key", input_mode=input_mode, output_mode=["text"]),
        client_registry=registry,
        default_agent="main",
        turn_registry=turn_registry,
    )
    registry.register(bridge.client_id, bridge)
    return bridge


def _end_meta(bridge) -> dict:
    ends = [e for e in bridge.tracker.events if e.stage == "session_end"]
    assert len(ends) == 1
    return ends[0].meta


def _closed() -> dict:
    return gateway_stats.snapshot()["sessions_closed"]


async def test_idle_timeout_reason(monkeypatch):
    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge = _make_bridge()
    bridge._last_client_activity = time.monotonic()
    await asyncio.wait_for(bridge._idle_watchdog(), timeout=1.0)
    assert _end_meta(bridge)["reason"] == "idle_timeout"
    assert _closed() == {"idle_timeout": 1}


async def test_silence_timeout_reason(monkeypatch):
    bridge = _make_bridge(ClientConfig(silence_timeout_s=1, max_silence_turns=1), "audio")
    transcriber = MagicMock()
    transcriber.state = ConnectionState.CONNECTED
    transcriber.last_transcript_at = None
    transcriber.close = AsyncMock()
    bridge.transcriber = transcriber
    now = time.monotonic()
    bridge._last_audio_at = now
    bridge._audio_burst_start = now - 5
    bridge._first_audio_at = now - 5

    async def _fast_sleep(_):
        bridge._last_audio_at = time.monotonic()  # cliente enviando audio sin transcripción

    monkeypatch.setattr("src.services.bridge.asyncio.sleep", _fast_sleep)
    await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "silence_timeout"
    assert _closed() == {"silence_timeout": 1}


async def test_revoked_reason(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_REVALIDATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "src.services.bridge.db_client.get_session", AsyncMock(side_effect=ClientInactive())
    )
    bridge = _make_bridge()
    await asyncio.wait_for(bridge._session_revalidation_loop(), timeout=1.0)
    assert _end_meta(bridge)["reason"] == "revoked"
    assert _closed() == {"revoked": 1}


async def test_client_disconnect_reason():
    bridge = _make_bridge()
    bridge.client_ws.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "client_disconnect"
    assert _closed() == {"client_disconnect": 1}
    assert gateway_stats.snapshot()["sessions_opened"] == 1


async def test_input_loop_exception_reason_is_error():
    bridge = _make_bridge()
    bridge.client_ws.receive = AsyncMock(side_effect=ValueError("boom"))
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "error"
    assert _closed() == {"error": 1}


async def test_session_counted_opened_without_run():
    bridge = _make_bridge()
    await bridge.close_all(status="error")
    snap = gateway_stats.snapshot()
    assert snap["sessions_opened"] == 1
    assert snap["sessions_closed"] == {"error": 1}


async def test_shutdown_reason():
    bridge = _make_bridge()
    await bridge._client_registry.close_all_sessions(status="shutdown", timeout=1.0)
    assert _end_meta(bridge)["reason"] == "shutdown"
    assert _closed() == {"shutdown": 1}


async def test_reason_derived_from_status_when_not_noted():
    bridge = _make_bridge()
    await bridge.close_all(status="error")
    assert _end_meta(bridge)["reason"] == "error"
    bridge2 = _make_bridge()
    await bridge2.close_all()
    assert _end_meta(bridge2)["reason"] == "unknown"
    assert _closed() == {"error": 1, "unknown": 1}


async def test_first_close_reason_wins():
    bridge = _make_bridge()
    await bridge.close_all(reason="idle_timeout")
    bridge.note_close_reason("client_disconnect")
    await bridge.close_all()  # la llamada de run()
    await bridge.close_all(status="error")  # la de routes.py
    assert _end_meta(bridge)["reason"] == "idle_timeout"
    assert _closed() == {"idle_timeout": 1}


async def test_session_closed_counted_once_when_close_all_is_retried():
    """#101: un close_all interrumpido se reintenta; el contador sube una sola vez."""
    bridge = _make_bridge()
    real_close = bridge.tracker.close
    calls = 0

    async def _flaky_close(**kw):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        await real_close(**kw)

    bridge.tracker.close = _flaky_close
    try:
        await bridge.close_all(reason="idle_timeout")
    except RuntimeError:
        pass
    assert not bridge._closed
    await bridge.close_all()
    assert bridge._closed
    assert _closed() == {"idle_timeout": 1}


async def test_turn_in_flight_flag():
    bridge = _make_bridge()
    gate = asyncio.Event()
    bridge._active_turn = asyncio.create_task(gate.wait())
    bridge._client_gone = True  # cancela el turno en vez de drenarlo
    await bridge.close_all()
    assert _end_meta(bridge)["turn_in_flight"] is True

    idle = _make_bridge()
    await idle.close_all()
    assert _end_meta(idle)["turn_in_flight"] is False


async def test_queue_peaks_in_session_end():
    turns = TurnRegistry()
    bridge = _make_bridge(turn_registry=turns)
    sk = make_session_key("main", bridge.client_id)
    q = turns.register("req-1", sk)
    turns.deliver_by_session(sk, ("token", "a"))
    turns.deliver_by_session(sk, ("token", "b"))
    turns.unregister(sk, "req-1")
    assert q.qsize() == 2
    bridge._push_pending_peak = 3
    await bridge.close_all()
    meta = _end_meta(bridge)
    assert meta["turn_queue_peak"] == 2
    assert meta["push_backlog_peak"] == 3
    assert meta["sender_peak_msgs"] == 0 and meta["sender_peak_bytes"] == 0
    assert turns.depth(sk) == (0, 0)  # pop_peak consumido


async def test_queue_peaks_default_to_zero_without_turn_registry():
    bridge = _make_bridge()
    await bridge.close_all()
    assert _end_meta(bridge)["turn_queue_peak"] == 0


async def test_server_shutdown_disconnect_1012_records_shutdown():
    """uvicorn cierra cada WS con 1012 antes del shutdown del lifespan."""
    from fastapi import WebSocketDisconnect

    bridge = _make_bridge()
    bridge.client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect(code=1012))
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "shutdown"
    assert _closed() == {"shutdown": 1}


async def test_server_shutdown_disconnect_message_1012_records_shutdown():
    bridge = _make_bridge()
    bridge.client_ws.receive = AsyncMock(
        return_value={"type": "websocket.disconnect", "code": 1012}
    )
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "shutdown"


async def test_normal_disconnect_codes_record_client_disconnect():
    from fastapi import WebSocketDisconnect

    for code in (1000, 1001):
        bridge = _make_bridge()
        bridge.client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect(code=code))
        await asyncio.wait_for(bridge.run(), timeout=2.0)
        assert _end_meta(bridge)["reason"] == "client_disconnect"
    assert _closed() == {"client_disconnect": 2}
