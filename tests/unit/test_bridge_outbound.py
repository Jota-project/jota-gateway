"""JotaBridge sends everything through its OutboundSender (issue #130 S1)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.outbound import ClientGone, QueuedSender
from src.services.pipeline_tracker import PipelineTracker
from src.services.protocol import OrchestratorEvent

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)


class RecordingWS:
    def __init__(self, fail_on_bytes: bool = False):
        self.sent: list[tuple[str, object]] = []
        self._fail_on_bytes = fail_on_bytes

    async def _send(self, kind, payload):
        await asyncio.sleep(0)
        if self._fail_on_bytes and kind == "bytes":
            raise RuntimeError("socket roto")
        self.sent.append((kind, payload))

    async def send_json(self, payload):
        await self._send("json", payload)

    async def send_bytes(self, data):
        await self._send("bytes", data)


def _make(ws, output_mode=("text", "status"), tts=None):
    sender = QueuedSender(ws)
    tracker = PipelineTracker(
        session_id="s:1",
        client_id="test-uuid",
        input_mode="text",
        output_mode=list(output_mode),
        client_ws=ws,
        registry=MagicMock(),
        sender=sender,
    )
    bridge = JotaBridge(
        client=_CLIENT,
        config=ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=tts or AsyncMock(),
        tracker=tracker,
        handshake=Handshake(client_key="test-key", input_mode="text", output_mode=list(output_mode)),
        client_registry=ClientRegistry(),
        default_agent="main",
        sender=sender,
    )
    bridge.transcriber = None
    return bridge, sender


def _stream(*tokens):
    async def _s(*args, **kwargs):
        for t in tokens:
            yield OrchestratorEvent(type="token", content=t)

    return _s


def _types(ws):
    return [p["type"] for k, p in ws.sent if k == "json"]


async def test_turn_messages_leave_through_sender_in_order():
    ws = RecordingWS()
    bridge, sender = _make(ws)
    bridge.orchestrator.stream_response = _stream("a", "b")

    await bridge._call_orchestrator("hola")
    await sender.aclose(1.0)

    types = _types(ws)
    assert types[0] == "turn_start"
    assert types[-1] == "turn_end"
    assert types.count("token") == 2
    assert "pipeline_event" in types
    # pipeline_event never jumps ahead of the turn_start that opened the turn
    assert all(i > types.index("turn_start") for i, t in enumerate(types) if t == "pipeline_event")


async def test_turn_end_queued_before_close_is_delivered():
    ws = RecordingWS()
    bridge, sender = _make(ws)
    bridge.orchestrator.stream_response = _stream("a")

    await bridge._call_orchestrator("hola")
    await bridge.close_all()  # enqueues session_end via tracker.close()
    await sender.aclose(1.0)

    types = _types(ws)
    assert "turn_end" in types
    assert types[-1] == "pipeline_event"  # session_end is the last thing out
    assert ws.sent[-1][1]["stage"] == "session_end"


async def test_broken_sender_stops_pipe_audio_and_turn_still_finishes():
    ws = RecordingWS(fail_on_bytes=True)  # text writes work; first audio write fails
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
    bridge, sender = _make(ws, output_mode=("text", "audio"), tts=tts)
    bridge.orchestrator.stream_response = _stream("a")

    await asyncio.wait_for(bridge._call_orchestrator("hola"), timeout=2.0)

    assert pulled >= 1  # pipe_audio really ran
    assert pulled < total  # ...and stopped once the sender broke
    assert not any(k == "bytes" for k, _ in ws.sent)  # no audio got through
    tts_client.close.assert_awaited()
    with pytest.raises(ClientGone):
        await sender.send_json({"type": "x"})
    await sender.aclose(1.0)


async def test_bridge_defaults_to_direct_sender_forwarding_to_client_ws():
    ws = AsyncMock()
    tracker = PipelineTracker(
        session_id="s:1", client_id="c", input_mode="text", output_mode=["text"],
        client_ws=ws, registry=MagicMock(),
    )
    bridge = JotaBridge(
        client=_CLIENT, config=ClientConfig(), client_ws=ws, orchestrator=AsyncMock(),
        tts=AsyncMock(), tracker=tracker,
        handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
        client_registry=ClientRegistry(), default_agent="main",
    )
    await bridge.notify_service_status("tts", "restored")
    ws.send_json.assert_awaited_once_with(
        {"type": "status", "service": "tts", "state": "restored"}
    )


async def _broken(ws):
    bridge, sender = _make(ws)
    await sender.send_bytes(b"x")  # first bytes write fails -> sender broken
    await sender._writer
    return bridge, sender


async def test_notify_service_status_propagates_when_asked():
    bridge, _sender = await _broken(RecordingWS(fail_on_bytes=True))
    with pytest.raises(ClientGone):
        await bridge.notify_service_status("tts", "unavailable", propagate=True)
    # default: swallowed, as before
    await bridge.notify_service_status("tts", "unavailable")


async def test_health_check_orchestrator_down_sends_status_via_sender_and_returns_none():
    ws = RecordingWS()
    bridge, sender = _make(ws)
    bridge.orchestrator.ping = AsyncMock(return_value=False)
    assert await bridge.health_check() is None
    await sender.aclose(1.0)
    assert ws.sent == [("json", {"type": "status", "service": "orchestrator", "state": "unavailable"})]


async def test_health_check_still_propagates_send_failure():
    bridge, _sender = await _broken(RecordingWS(fail_on_bytes=True))
    bridge.orchestrator.ping = AsyncMock(return_value=False)
    with pytest.raises(ClientGone):
        await bridge.health_check()


async def test_broken_sender_stops_push_audio_and_push_turn_end_completes():
    ws = RecordingWS(fail_on_bytes=True)
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
    bridge, sender = _make(ws, output_mode=("text", "audio"), tts=tts)

    await bridge.on_push_turn_start("agent:main:test-uuid")
    await asyncio.wait_for(bridge._push_audio_task, timeout=2.0)

    assert pulled >= 1  # _pipe_push_audio really ran
    assert pulled < total  # ...and stopped once the sender broke
    assert not any(k == "bytes" for k, _ in ws.sent)
    await asyncio.wait_for(bridge.on_push_turn_end("agent:main:test-uuid"), timeout=2.0)
    await sender.aclose(1.0)
