"""#199 / #204: topes de tiempo del TTS de push y endurecimiento del worker."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from src.core.config import settings
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry


def make_bridge(output_mode=("audio", "text")):
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=list(output_mode), agent="main"
    )
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=None)
    return JotaBridge(
        client=client,
        config=ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=tts,
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )


def _open_turn(bridge, tts):
    bridge._push_tts = tts
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True


async def _hang(*_a):
    await asyncio.sleep(3600)


def _tokens(bridge):
    return [
        c.args[0]["text"]
        for c in bridge.client_ws.send_json.await_args_list
        if c.args[0].get("type") == "token"
    ]


@pytest.mark.asyncio
async def test_hung_send_text_chunk_drops_tts_and_continues_text_only(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_bridge()
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
    assert _tokens(bridge) == ["a", "b", "c"]
    assert len([r for r in caplog.records if "TTS de push" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_failing_tts_logs_once_per_turn(caplog):
    bridge = make_bridge()
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=ConnectionError("boom"))
    _open_turn(bridge, tts)

    with caplog.at_level(logging.WARNING):
        for d in "abc":
            await bridge.deliver_push({"deltaText": d})

    assert tts.send_text_chunk.await_count == 1
    assert bridge._push_tts is None
    assert _tokens(bridge) == ["a", "b", "c"]
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


@pytest.mark.asyncio
async def test_hung_tts_end_still_sends_turn_end(monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_bridge()
    tts = AsyncMock()
    tts.end = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)
    stuck = asyncio.create_task(asyncio.sleep(3600))
    bridge._push_audio_task = stuck

    await asyncio.wait_for(bridge.on_push_turn_end("sk"), timeout=2.0)

    assert stuck.cancelled()
    tts.close.assert_awaited_once()
    bridge.client_ws.send_json.assert_any_call({"type": "turn_end", "turn_id": "t-1"})
    assert bridge._push_tts is None
    assert bridge._push_turn_open is False


@pytest.mark.asyncio
async def test_hung_tts_close_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_bridge()
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    tts.close = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)

    await asyncio.wait_for(bridge.deliver_push({"deltaText": "a"}), timeout=2.0)

    assert bridge._push_tts is None


@pytest.mark.asyncio
async def test_hung_tts_does_not_wedge_worker(monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_bridge(output_mode=("text",))
    tts = AsyncMock()
    tts.send_text_chunk = AsyncMock(side_effect=_hang)
    _open_turn(bridge, tts)

    bridge.enqueue_push("chat", {"deltaText": "a"})
    bridge.enqueue_push("turn_end", "sk")
    await asyncio.wait_for(bridge.push_idle(), timeout=2.0)

    bridge.client_ws.send_json.assert_any_call({"type": "turn_end", "turn_id": "t-1"})
    await bridge.close_all()


@pytest.mark.asyncio
async def test_on_push_turn_end_bounds_gather_after_cancel(monkeypatch):
    """#204: un audio que ignora la cancelación no cuelga on_push_turn_end."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge = make_bridge()
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
        bridge.client_ws.send_json.assert_any_call({"type": "turn_end", "turn_id": "t-1"})
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_enqueue_push_respawns_a_dead_worker():
    bridge = make_bridge(output_mode=("text",))
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
    assert _tokens(bridge) == ["a", "b"]
    await bridge.close_all()


@pytest.mark.asyncio
async def test_worker_exits_when_not_accepting():
    bridge = make_bridge(output_mode=("text",))

    async def end_and_stop(sk):
        bridge._push_accepting = False

    bridge.on_push_turn_end = end_and_stop
    bridge.enqueue_push("turn_end", "sk")
    await asyncio.wait_for(bridge._push_worker, timeout=2.0)
    assert bridge._push_worker.done()
    await bridge.close_all()
