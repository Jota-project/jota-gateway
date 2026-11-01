"""#199 / #204: topes de tiempo del TTS de push y endurecimiento del worker."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from src.core.config import settings
from tests.fakes.bridge import sent


@pytest.fixture
def make_push_bridge(make_bridge):
    def _make(output_mode=("audio", "text")):
        return make_bridge(client_id="hab_sito", output_mode=output_mode)[0]

    return _make


def _open_turn(bridge, tts):
    bridge._push_tts = tts
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True


async def _hang(*_a):
    await asyncio.sleep(3600)


async def _tokens(bridge):
    return [m["text"] for m in await sent(bridge) if isinstance(m, dict) and m["type"] == "token"]


async def test_hung_send_text_chunk_drops_tts_and_continues_text_only(make_push_bridge, monkeypatch, caplog):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_push_bridge()
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)
    stuck = asyncio.create_task(asyncio.sleep(3600))
    bridge._push_audio_task = stuck

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(bridge.deliver_push({"deltaText": "a"}), timeout=2.0)
        await bridge.deliver_push({"deltaText": "b"})
        await bridge.deliver_push({"deltaText": "c"})

    assert bridge._push_tts is None
    assert stuck.cancelled()
    tts.close.assert_awaited_once()
    tts.send_text_chunk.assert_awaited_once()
    assert await _tokens(bridge) == ["a", "b", "c"]
    assert len([r for r in caplog.records if "TTS de push" in r.getMessage()]) == 1


async def test_failing_tts_logs_once_per_turn(make_push_bridge, caplog):
    bridge = make_push_bridge()
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=ConnectionError("boom"))
    _open_turn(bridge, tts)

    with caplog.at_level(logging.WARNING):
        for d in "abc":
            await bridge.deliver_push({"deltaText": d})

    assert tts.send_text_chunk.await_count == 1
    assert bridge._push_tts is None
    assert await _tokens(bridge) == ["a", "b", "c"]
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


async def test_hung_tts_end_still_sends_turn_end(make_push_bridge, monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_push_bridge()
    tts = AsyncMock()
    tts.end = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)
    stuck = asyncio.create_task(asyncio.sleep(3600))
    bridge._push_audio_task = stuck

    await asyncio.wait_for(bridge.on_push_turn_end("sk"), timeout=2.0)

    assert stuck.cancelled()
    tts.close.assert_awaited_once()
    assert {"type": "turn_end", "turn_id": "t-1"} in await sent(bridge)
    assert bridge._push_tts is None
    assert bridge._push_turn_open is False


async def test_hung_tts_close_is_bounded(make_push_bridge, monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_push_bridge()
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    tts.close = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)

    await asyncio.wait_for(bridge.deliver_push({"deltaText": "a"}), timeout=2.0)

    assert bridge._push_tts is None


async def test_hung_tts_does_not_wedge_worker(make_push_bridge, monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_push_bridge(output_mode=("text",))
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)

    bridge.enqueue_push("chat", {"deltaText": "a"})
    bridge.enqueue_push("turn_end", "sk")
    await asyncio.wait_for(bridge.push_idle(), timeout=2.0)

    assert {"type": "turn_end", "turn_id": "t-1"} in await sent(bridge)
    await bridge.close_all()


async def test_on_push_turn_end_bounds_gather_after_cancel(make_push_bridge, monkeypatch):
    """#204: un audio que ignora la cancelación no cuelga on_push_turn_end."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_push_bridge()
    _open_turn(bridge, AsyncMock())
    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    task = asyncio.create_task(stubborn())
    bridge._push_audio_task = task
    try:
        await asyncio.wait_for(bridge.on_push_turn_end("sk"), timeout=2.0)
        assert {"type": "turn_end", "turn_id": "t-1"} in await sent(bridge)
    finally:
        release.set()
        await task


async def test_enqueue_push_respawns_a_dead_worker(make_push_bridge):
    bridge = make_push_bridge(output_mode=("text",))
    _open_turn(bridge, None)
    bridge._push_tts = None
    bridge.enqueue_push("chat", {"deltaText": "a"})
    await bridge.push_idle()
    first = bridge._push_worker
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert first.done()

    bridge.enqueue_push("chat", {"deltaText": "b"})
    await asyncio.wait_for(bridge.push_idle(), timeout=2.0)

    assert bridge._push_worker is not first
    assert await _tokens(bridge) == ["a", "b"]
    await bridge.close_all()


async def test_worker_exits_when_not_accepting(make_push_bridge):
    bridge = make_push_bridge(output_mode=("text",))

    async def end_and_stop(sk):
        bridge._closing = True

    bridge.on_push_turn_end = end_and_stop
    bridge.enqueue_push("turn_end", "sk")
    await asyncio.wait_for(bridge._push_worker, timeout=2.0)
    assert bridge._push_worker.done()
    await bridge.close_all()


def _push_events(bridge):
    return [c for c in bridge.tracker.record.await_args_list if c.args[0] == "push_tts"]


async def _open_push(bridge, tts):
    bridge.tts.connect = AsyncMock(return_value=tts)
    bridge.tts.resolve_voice = lambda v: v
    await bridge.on_push_turn_start("sk")


async def test_push_tts_event_records_durations(make_push_bridge):
    bridge = make_push_bridge()
    tts = AsyncMock()

    async def slow_end():
        await asyncio.sleep(0.05)

    tts.end = slow_end

    async def no_audio():
        return
        yield

    tts.get_audio_stream = no_audio
    await _open_push(bridge, tts)
    await bridge.deliver_push({"deltaText": "hola "})
    await bridge.deliver_push({"deltaText": "mundo"})
    await bridge.on_push_turn_end("sk")

    (ev,) = _push_events(bridge)
    meta = ev.kwargs
    assert meta["end_ms"] >= 40
    assert meta["dropped"] is False
    assert meta["chars"] == len("hola mundo")
    assert {"send_ms", "drain_ms", "close_ms"} <= meta.keys()


async def test_push_tts_event_marks_dropped(make_push_bridge):
    bridge = make_push_bridge()
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=ConnectionError("boom"))
    await _open_push(bridge, tts)
    await bridge.deliver_push({"deltaText": "a"})
    await bridge.on_push_turn_end("sk")

    (ev,) = _push_events(bridge)
    assert ev.kwargs["dropped"] is True


async def test_no_push_tts_event_without_tts(make_push_bridge):
    bridge = make_push_bridge()
    await _open_push(bridge, None)
    await bridge.on_push_turn_end("sk")
    assert _push_events(bridge) == []


async def test_turn_counters(make_push_bridge, monkeypatch):
    from src.services import orchestration
    from src.services.stats import gateway_stats

    bridge = make_push_bridge(output_mode=("text",))
    bridge.tracker.start_turn = lambda: None
    monkeypatch.setattr(orchestration, "call_orchestrator", AsyncMock())
    await bridge._call_orchestrator("hola")
    await _open_push(bridge, None)
    monkeypatch.setattr(
        orchestration, "call_orchestrator", AsyncMock(side_effect=orchestration.OrchestratorError("orchestrator_error"))
    )
    await bridge._call_orchestrator("otra")

    snap = gateway_stats.snapshot()
    assert snap["turns"] == {"normal": 2, "push": 1}
    assert snap["turn_errors"]["orchestrator_error"] == 1


async def test_plain_runtime_error_counts_as_internal_error(make_push_bridge, monkeypatch):
    from src.services import orchestration
    from src.services.stats import gateway_stats

    bridge = make_push_bridge(output_mode=("text",))
    bridge.tracker.start_turn = lambda: None
    monkeypatch.setattr(
        orchestration, "call_orchestrator", AsyncMock(side_effect=RuntimeError("texto-arbitrario"))
    )
    await bridge._call_orchestrator("hola")

    errors = gateway_stats.snapshot()["turn_errors"]
    assert errors == {"internal_error": 1}
    assert "texto-arbitrario" not in errors


async def test_unexpected_exception_counts_as_internal_error(make_push_bridge, monkeypatch):
    from src.services import orchestration
    from src.services.stats import gateway_stats

    bridge = make_push_bridge(output_mode=("text",))
    bridge.tracker.start_turn = lambda: None
    monkeypatch.setattr(
        orchestration, "call_orchestrator", AsyncMock(side_effect=ValueError("x"))
    )
    await bridge._call_orchestrator("hola")
    assert gateway_stats.snapshot()["turn_errors"] == {"internal_error": 1}
