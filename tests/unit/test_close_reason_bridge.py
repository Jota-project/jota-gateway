"""Motivo de cierre de la sesión en session_end y en gateway_stats (#219, 6g)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from fogar_gateway.core.config import settings
from fogar_gateway.core.exceptions import ClientInactive
from fogar_gateway.core.session_key import make_session_key
from fogar_gateway.models.schemas import ClientConfig
from fogar_gateway.services.openclaw.registry import TurnRegistry
from fogar_gateway.services.pipeline_tracker import PipelineTracker
from fogar_gateway.services.protocol import OrchestratorEvent
from fogar_gateway.services.reconnection import ConnectionState
from fogar_gateway.services.stats import gateway_stats
from tests.fakes.bridge import DirectSender


@pytest.fixture
def make_reason_bridge(make_bridge):
    def _make(config=None, input_mode="text", turn_registry=None):
        # El tracker sigue con DirectSender: lo que importa aquí es su session_end.
        tracker = PipelineTracker(
            session_id="test:reason",
            client_id="hab_sito",
            input_mode=input_mode,
            output_mode=["text"],
            sender=DirectSender(AsyncMock()),
            registry=MagicMock(),
        )
        bridge, _ = make_bridge(
            client_id="hab_sito",
            config=config or ClientConfig(),
            input_mode=input_mode,
            tracker=tracker,
            turn_registry=turn_registry or TurnRegistry(),
            register=True,
        )
        return bridge

    return _make


def _end_meta(bridge) -> dict:
    ends = [e for e in bridge.tracker.events if e.stage == "session_end"]
    assert len(ends) == 1
    return ends[0].meta


def _closed() -> dict:
    return gateway_stats.snapshot()["sessions_closed"]


async def test_idle_timeout_reason(make_reason_bridge, monkeypatch):
    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.05)
    bridge = make_reason_bridge()
    bridge._last_client_activity = time.monotonic()
    await asyncio.wait_for(bridge._idle_watchdog(), timeout=1.0)
    assert _end_meta(bridge)["reason"] == "idle_timeout"
    assert _closed() == {"idle_timeout": 1}


async def test_silence_timeout_reason(make_reason_bridge, monkeypatch):
    bridge = make_reason_bridge(ClientConfig(silence_timeout_s=1, max_silence_turns=1), "audio")
    transcriber = MagicMock()
    transcriber.state = ConnectionState.CONNECTED
    transcriber.last_transcript_at = None
    transcriber.close = AsyncMock()
    bridge.transcriber = transcriber
    now = time.monotonic()
    bridge._last_audio_at = now
    bridge._audio_burst_start = now - 5
    bridge._last_audio_at = bridge._audio_burst_start = now - 5

    async def _fast_sleep(_):
        bridge._last_audio_at = time.monotonic()  # cliente enviando audio sin transcripción

    monkeypatch.setattr("fogar_gateway.services.bridge._sleep", _fast_sleep)
    await asyncio.wait_for(bridge._transcription_watchdog(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "silence_timeout"
    assert _closed() == {"silence_timeout": 1}


async def test_revoked_reason(make_reason_bridge, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_REVALIDATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "fogar_gateway.services.bridge.db_client.get_session", AsyncMock(side_effect=ClientInactive())
    )
    bridge = make_reason_bridge()
    await asyncio.wait_for(bridge._session_revalidation_loop(), timeout=1.0)
    assert _end_meta(bridge)["reason"] == "revoked"
    assert _closed() == {"revoked": 1}


async def test_client_disconnect_reason(make_reason_bridge):
    bridge = make_reason_bridge()
    bridge.client_ws.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "client_disconnect"
    assert _closed() == {"client_disconnect": 1}
    assert gateway_stats.snapshot()["sessions_opened"] == 1


async def test_input_loop_exception_reason_is_error(make_reason_bridge):
    bridge = make_reason_bridge()
    bridge.client_ws.receive = AsyncMock(side_effect=ValueError("boom"))
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "error"
    assert _closed() == {"error": 1}


async def test_session_counted_opened_without_run(make_reason_bridge):
    bridge = make_reason_bridge()
    await bridge.close_all(status="error")
    snap = gateway_stats.snapshot()
    assert snap["sessions_opened"] == 1
    assert snap["sessions_closed"] == {"error": 1}


async def test_reason_derived_from_status_when_not_noted(make_reason_bridge):
    bridge = make_reason_bridge()
    await bridge.close_all(status="error")
    assert _end_meta(bridge)["reason"] == "error"
    bridge2 = make_reason_bridge()
    await bridge2.close_all()
    assert _end_meta(bridge2)["reason"] == "unknown"
    assert _closed() == {"error": 1, "unknown": 1}


async def test_first_close_reason_wins(make_reason_bridge):
    bridge = make_reason_bridge()
    await bridge.close_all(reason="idle_timeout")
    bridge.note_close_reason("client_disconnect")
    await bridge.close_all()  # la llamada de run()
    await bridge.close_all(status="error")  # la de routes.py
    assert _end_meta(bridge)["reason"] == "idle_timeout"
    assert _closed() == {"idle_timeout": 1}


async def test_session_closed_counted_once_when_tracker_close_fails(make_reason_bridge):
    """#101/#221: un paso del cierre que falla (aquí tracker.close) no aborta el cierre
    ni se reintenta: el fallo va a DEBUG, la sesión queda cerrada y el contador sube
    una sola vez aunque se llame otra vez a close_all."""
    bridge = make_reason_bridge()

    async def _failing_close(**kw):
        raise RuntimeError("boom")

    bridge.tracker.close = _failing_close
    await bridge.close_all(reason="idle_timeout")
    assert bridge._closed
    await bridge.close_all()
    assert _closed() == {"idle_timeout": 1}


async def test_turn_in_flight_flag(make_reason_bridge):
    bridge = make_reason_bridge()
    started = asyncio.Event()

    async def forever(*a, **k):
        started.set()
        await asyncio.sleep(3600)
        yield OrchestratorEvent(type="token", content="x")  # pragma: no cover

    bridge.orchestrator.stream_response = forever
    await bridge.turns.start_user_turn("x")
    await asyncio.wait_for(started.wait(), 2)
    await bridge.close_all()
    assert _end_meta(bridge)["turn_in_flight"] is True

    idle = make_reason_bridge()
    await idle.close_all()
    assert _end_meta(idle)["turn_in_flight"] is False


async def test_queue_peaks_in_session_end(make_reason_bridge):
    turns = TurnRegistry()
    bridge = make_reason_bridge(turn_registry=turns)
    sk = make_session_key("main", bridge.client_id)
    q = turns.register("req-1", sk)
    turns.deliver_by_session(sk, ("token", "a"))
    turns.deliver_by_session(sk, ("token", "b"))
    turns.unregister(sk, "req-1")
    assert q.qsize() == 2
    await bridge.turns._lock.acquire()  # a turn being replaced: the push worker waits
    for kind in ("turn_start", "chat", "chat"):
        bridge.enqueue_push(kind, {})
    await asyncio.sleep(0.01)
    await bridge.close_all()
    bridge.turns._lock.release()
    meta = _end_meta(bridge)
    assert meta["turn_queue_peak"] == 2
    assert meta["push_backlog_peak"] == 3
    assert meta["sender_peak_msgs"] == 0 and meta["sender_peak_bytes"] == 0
    assert turns.depth(sk) == (0, 0)  # pop_peak consumido


async def test_queue_peaks_default_to_zero_without_a_turn(make_reason_bridge):
    bridge = make_reason_bridge()
    await bridge.close_all()
    assert _end_meta(bridge)["turn_queue_peak"] == 0


async def test_server_shutdown_disconnect_1012_records_shutdown(make_reason_bridge):
    """uvicorn cierra cada WS con 1012 antes del shutdown del lifespan."""
    from fastapi import WebSocketDisconnect

    bridge = make_reason_bridge()
    bridge.client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect(code=1012))
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "shutdown"
    assert _closed() == {"shutdown": 1}


async def test_server_shutdown_disconnect_message_1012_records_shutdown(make_reason_bridge):
    bridge = make_reason_bridge()
    bridge.client_ws.receive = AsyncMock(
        return_value={"type": "websocket.disconnect", "code": 1012}
    )
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert _end_meta(bridge)["reason"] == "shutdown"


async def test_normal_disconnect_codes_record_client_disconnect(make_reason_bridge):
    from fastapi import WebSocketDisconnect

    for code in (1000, 1001):
        bridge = make_reason_bridge()
        bridge.client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect(code=code))
        await asyncio.wait_for(bridge.run(), timeout=2.0)
        assert _end_meta(bridge)["reason"] == "client_disconnect"
    assert _closed() == {"client_disconnect": 2}
