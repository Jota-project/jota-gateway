"""#199 / #204 / #271: topes de tiempo del TTS de push (el mismo `TurnTts` que el turno
normal), endurecimiento del worker y métricas."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from fogar_gateway.core.config import settings
from tests.fakes.bridge import push, push_reply, run_turn, sent, tokens

SK = "agent:main:hab_sito"


@pytest.fixture
def make_push_bridge(make_bridge):
    def _make(output_mode=("audio", "text"), tts=None):
        provider = None
        if tts is not None:
            provider = AsyncMock()
            provider.connect = AsyncMock(return_value=tts)
            provider.resolve_voice = lambda v: v
        return make_bridge(client_id="hab_sito", output_mode=output_mode, tts=provider)[0]

    return _make


async def _hang(*_a):
    await asyncio.sleep(3600)


def _stream(*, audio=None):
    stream = AsyncMock()

    async def no_audio():
        return
        yield

    stream.get_audio_stream = audio or no_audio
    return stream


async def _tokens(bridge):
    return [m["text"] for m in await sent(bridge) if isinstance(m, dict) and m["type"] == "token"]


async def _turn_end(bridge):
    return [m for m in await sent(bridge) if isinstance(m, dict) and m["type"] == "turn_end"]


async def test_hung_send_text_chunk_drops_tts_and_continues_text_only(make_push_bridge, monkeypatch, caplog):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    audio_cancelled = asyncio.Event()

    async def audio():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            audio_cancelled.set()
            raise
        yield b""  # pragma: no cover

    tts = _stream(audio=audio)
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    bridge = make_push_bridge(tts=tts)

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(push(bridge, *push_reply("a", "b", "c", sk=SK)), timeout=2.0)

    assert audio_cancelled.is_set()
    tts.close.assert_awaited_once()
    tts.send_text_chunk.assert_awaited_once()
    assert await _tokens(bridge) == ["a", "b", "c"]
    assert await _turn_end(bridge) == [{"type": "turn_end", "turn_id": "t-1"}]
    assert len([r for r in caplog.records if "TTS del turno" in r.getMessage()]) == 1


async def test_failing_tts_logs_once_per_turn(make_push_bridge, caplog):
    tts = _stream()
    tts.send_text_chunk = AsyncMock(side_effect=ConnectionError("boom"))
    bridge = make_push_bridge(tts=tts)

    with caplog.at_level(logging.WARNING):
        await push(bridge, *push_reply("a", "b", "c", sk=SK))

    assert tts.send_text_chunk.await_count == 1
    assert await _tokens(bridge) == ["a", "b", "c"]
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


async def test_hung_tts_end_still_sends_turn_end(make_push_bridge, monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)

    async def audio():
        await asyncio.sleep(3600)
        yield b""  # pragma: no cover

    tts = _stream(audio=audio)
    tts.end = AsyncMock(side_effect=_hang)
    bridge = make_push_bridge(tts=tts)

    await asyncio.wait_for(push(bridge, *push_reply("a", sk=SK)), timeout=2.0)

    tts.close.assert_awaited_once()
    assert await _turn_end(bridge) == [{"type": "turn_end", "turn_id": "t-1"}]
    assert bridge.turns.snapshot(0.0)["turn"]["kind"] == "idle"


async def test_hung_tts_close_is_bounded(make_push_bridge, monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    tts = _stream()
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    tts.close = AsyncMock(side_effect=_hang)
    bridge = make_push_bridge(tts=tts)

    await asyncio.wait_for(push(bridge, *push_reply("a", sk=SK)), timeout=2.0)

    assert await _turn_end(bridge) == [{"type": "turn_end", "turn_id": "t-1"}]


async def test_audio_ignoring_cancellation_does_not_hang_the_push(make_push_bridge, monkeypatch):
    """#204: un audio que ignora la cancelación no cuelga el fin del push."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return
        yield  # pragma: no cover

    bridge = make_push_bridge(tts=_stream(audio=stubborn))
    try:
        await asyncio.wait_for(push(bridge, *push_reply("a", sk=SK)), timeout=2.0)
        assert await _turn_end(bridge) == [{"type": "turn_end", "turn_id": "t-1"}]
    finally:
        release.set()
        await asyncio.sleep(0)


async def test_enqueue_push_respawns_a_dead_worker(make_push_bridge):
    bridge = make_push_bridge(output_mode=("text",))
    await push(bridge, ("turn_start", SK), ("chat", {"deltaText": "a"}))
    (first,) = [t for t in bridge.tasks if t.get_name() == "push_worker"]
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)

    await asyncio.wait_for(push(bridge, ("chat", {"deltaText": "b"})), timeout=2.0)

    workers = [t for t in bridge.tasks if t.get_name() == "push_worker"]
    assert len(workers) == 2 and not workers[1].done()
    assert await _tokens(bridge) == ["a", "b"]
    await bridge.close_all()


def _push_events(bridge):
    return [c for c in bridge.tracker.record.await_args_list if c.args[0] == "push_tts"]


async def test_push_tts_event_records_durations(make_push_bridge):
    tts = _stream()

    async def slow_end():
        await asyncio.sleep(0.05)

    tts.end = slow_end
    bridge = make_push_bridge(tts=tts)
    await push(bridge, *push_reply("hola ", "mundo", sk=SK))

    (ev,) = _push_events(bridge)
    meta = ev.kwargs
    assert meta["end_ms"] >= 40
    assert meta["dropped"] is False
    assert meta["chars"] == len("hola mundo")
    assert {"send_ms", "drain_ms", "close_ms"} <= meta.keys()


async def test_push_tts_event_marks_dropped(make_push_bridge):
    tts = _stream()
    tts.send_text_chunk = AsyncMock(side_effect=ConnectionError("boom"))
    bridge = make_push_bridge(tts=tts)
    await push(bridge, *push_reply("a", sk=SK))

    (ev,) = _push_events(bridge)
    assert ev.kwargs["dropped"] is True


async def test_no_push_tts_event_without_tts(make_push_bridge):
    bridge = make_push_bridge()
    bridge.tts.connect = AsyncMock(return_value=None)  # the TTS is down: no stream
    await push(bridge, *push_reply("a", sk=SK))
    assert _push_events(bridge) == []


async def test_push_records_the_same_tts_metrics_as_a_normal_turn(make_push_bridge):
    """#271: tts_start, tts_first_chunk and tts_done for both origins (the push's tagged)."""

    async def audio():
        yield b"\x01\x02"

    bridge = make_push_bridge(tts=_stream(audio=audio))
    bridge.orchestrator.stream_response = tokens("a")
    await run_turn(bridge)
    await push(bridge, *push_reply("b", sk=SK))

    stages = [
        (c.args[0], c.kwargs.get("origin"))
        for c in bridge.tracker.record.await_args_list
        if c.args[0] in ("tts_start", "tts_first_chunk", "tts_done")
    ]
    assert stages == [
        ("tts_start", None), ("tts_first_chunk", None), ("tts_done", None),
        ("tts_start", "push"), ("tts_first_chunk", "push"), ("tts_done", "push"),
    ]  # fmt: skip


async def test_long_push_reply_is_not_cut_by_the_inactivity_cap(make_push_bridge, monkeypatch):
    """#271: a push that keeps producing audio for longer than the cap (with no pause
    longer than it) is delivered in full, ending included."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.3)
    total = 12

    async def audio():
        for i in range(total):
            await asyncio.sleep(0.05)
            yield bytes([i])

    bridge = make_push_bridge(tts=_stream(audio=audio))
    await asyncio.wait_for(push(bridge, *push_reply("una respuesta larga", sk=SK)), timeout=5.0)

    msgs = await sent(bridge)
    assert [m[3] for m in msgs if isinstance(m, bytes)] == list(range(total))
    assert msgs[-1] == {"type": "turn_end", "turn_id": "t-1"}


async def test_push_drain_window_restarts_at_drain_start(make_push_bridge, monkeypatch):
    """7e: tras una pausa larga del agente, el sello de progreso es viejo; el audio que
    llega poco después del `end` no se corta."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.3)
    ended = asyncio.Event()

    async def late_audio():
        await ended.wait()
        for _ in range(3):
            await asyncio.sleep(0.05)
            yield b"\x00" * 10

    tts = _stream(audio=late_audio)
    tts.end = AsyncMock(side_effect=lambda: ended.set())
    bridge = make_push_bridge(tts=tts)
    await push(bridge, ("turn_start", SK), ("chat", {"deltaText": "a"}))
    await asyncio.sleep(0.4)  # longer than the cap: the progress stamp is now stale
    await asyncio.wait_for(push(bridge, ("turn_end", SK)), timeout=2.0)

    msgs = await sent(bridge)
    assert len([m for m in msgs if isinstance(m, bytes)]) == 3
    assert msgs[-1] == {"type": "turn_end", "turn_id": "t-1"}


async def test_turn_counters(make_push_bridge, monkeypatch):
    from fogar_gateway.services import orchestration
    from fogar_gateway.services.stats import gateway_stats

    bridge = make_push_bridge(output_mode=("text",))
    monkeypatch.setattr(orchestration, "call_orchestrator", AsyncMock())
    await run_turn(bridge, "hola")
    await push(bridge, *push_reply("x", sk=SK))
    monkeypatch.setattr(
        orchestration, "call_orchestrator", AsyncMock(side_effect=orchestration.OrchestratorError("orchestrator_error"))
    )
    await run_turn(bridge, "otra")

    snap = gateway_stats.snapshot()
    assert snap["turns"] == {"normal": 2, "push": 1}
    assert snap["turn_errors"]["orchestrator_error"] == 1


async def test_plain_runtime_error_counts_as_internal_error(make_push_bridge, monkeypatch):
    from fogar_gateway.services import orchestration
    from fogar_gateway.services.stats import gateway_stats

    bridge = make_push_bridge(output_mode=("text",))
    monkeypatch.setattr(
        orchestration, "call_orchestrator", AsyncMock(side_effect=RuntimeError("texto-arbitrario"))
    )
    await run_turn(bridge, "hola")

    errors = gateway_stats.snapshot()["turn_errors"]
    assert errors == {"internal_error": 1}
    assert "texto-arbitrario" not in errors


async def test_unexpected_exception_counts_as_internal_error(make_push_bridge, monkeypatch):
    from fogar_gateway.services import orchestration
    from fogar_gateway.services.stats import gateway_stats

    bridge = make_push_bridge(output_mode=("text",))
    monkeypatch.setattr(
        orchestration, "call_orchestrator", AsyncMock(side_effect=ValueError("x"))
    )
    await run_turn(bridge, "hola")
    assert gateway_stats.snapshot()["turn_errors"] == {"internal_error": 1}
