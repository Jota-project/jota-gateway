"""JotaBridge sends everything through its OutboundSender (issue #130 S1)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.outbound import ClientGone, QueuedSender
from src.services.pipeline_tracker import PipelineTracker
from tests.fakes.bridge import GoneSender, RecWS, tokens


class BytesFailWS(RecWS):
    """Text writes work; the first audio write fails."""

    async def send_bytes(self, b):
        raise RuntimeError("socket roto")


@pytest.fixture
def make(make_bridge):
    def _make(ws, output_mode=("text", "status"), tts=None):
        sender = QueuedSender(ws)
        tracker = PipelineTracker(
            session_id="s:1",
            client_id="test-uuid",
            input_mode="text",
            output_mode=list(output_mode),
            registry=MagicMock(),
            sender=sender,
        )
        bridge, _ = make_bridge(
            ws, client_id="test-uuid", output_mode=output_mode, tts=tts,
            tracker=tracker, sender=sender,
        )
        return bridge, sender

    return _make


async def test_turn_messages_leave_through_sender_in_order(make):
    ws = RecWS()
    bridge, sender = make(ws)
    bridge.orchestrator.stream_response = tokens("a", "b")

    await bridge._call_orchestrator("hola")
    await sender.aclose(1.0)

    types = ws.types()
    assert types[0] == "turn_start"
    assert types[-1] == "turn_end"
    assert types.count("token") == 2
    assert "pipeline_event" in types
    # pipeline_event never jumps ahead of the turn_start that opened the turn
    assert all(i > types.index("turn_start") for i, t in enumerate(types) if t == "pipeline_event")


async def test_turn_end_queued_before_close_is_delivered(make):
    ws = RecWS()
    bridge, sender = make(ws)
    bridge.orchestrator.stream_response = tokens("a")

    await bridge._call_orchestrator("hola")
    await bridge.close_all()  # enqueues session_end via tracker.close()
    await sender.aclose(1.0)

    types = ws.types()
    assert "turn_end" in types
    assert types[-1] == "pipeline_event"  # session_end is the last thing out
    assert ws.sent[-1]["stage"] == "session_end"


async def test_broken_sender_stops_pipe_audio_and_turn_still_finishes(make):
    ws = BytesFailWS()  # text writes work; first audio write fails
    tts = AsyncMock()
    total = 50
    pulled = 0

    async def audio():
        nonlocal pulled
        for i in range(total):
            await asyncio.sleep(0)
            pulled += 1
            yield bytes([i])

    tts_client = AsyncMock()
    tts_client.get_audio_stream = audio
    tts.connect = AsyncMock(return_value=tts_client)
    bridge, sender = make(ws, output_mode=("text", "audio"), tts=tts)
    bridge.orchestrator.stream_response = tokens("a")

    await asyncio.wait_for(bridge._call_orchestrator("hola"), timeout=2.0)

    assert pulled >= 1  # pipe_audio really ran
    assert pulled < total  # ...and stopped once the sender broke
    assert not any(isinstance(m, bytes) for m in ws.sent)  # no audio got through
    tts_client.close.assert_awaited()
    with pytest.raises(ClientGone):
        await sender.send_json({"type": "x"})
    await sender.aclose(1.0)


async def test_notify_service_status_propagates_when_asked(make_bridge):
    bridge, _ = make_bridge(sender=GoneSender())
    with pytest.raises(ClientGone):
        await bridge.notify_service_status("tts", "unavailable", propagate=True)
    # default: swallowed, as before
    await bridge.notify_service_status("tts", "unavailable")


async def test_health_check_orchestrator_down_sends_status_via_sender_and_returns_none(make):
    ws = RecWS()
    bridge, sender = make(ws)
    bridge.orchestrator.ping = AsyncMock(return_value=False)
    assert await bridge.health_check() is None
    await sender.aclose(1.0)
    assert ws.sent == [{"type": "status", "service": "orchestrator", "state": "unavailable"}]


async def test_health_check_still_propagates_send_failure(make_bridge):
    bridge, _ = make_bridge(sender=GoneSender())
    bridge.orchestrator.ping = AsyncMock(return_value=False)
    with pytest.raises(ClientGone):
        await bridge.health_check()


async def test_broken_sender_stops_push_audio_and_push_turn_end_completes(make):
    ws = BytesFailWS()
    total = 50
    pulled = 0

    async def audio():
        nonlocal pulled
        for i in range(total):
            await asyncio.sleep(0)
            pulled += 1
            yield bytes([i])

    tts_client = AsyncMock()
    tts_client.get_audio_stream = audio
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=tts_client)
    bridge, sender = make(ws, output_mode=("text", "audio"), tts=tts)

    await bridge.on_push_turn_start("agent:main:test-uuid")
    await asyncio.wait_for(bridge._push_audio_task, timeout=2.0)

    assert pulled >= 1  # _pipe_push_audio really ran
    assert pulled < total  # ...and stopped once the sender broke
    assert not any(isinstance(m, bytes) for m in ws.sent)
    await asyncio.wait_for(bridge.on_push_turn_end("agent:main:test-uuid"), timeout=2.0)
    await sender.aclose(1.0)
