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
    def __init__(self, fail_on: int | None = None):
        self.sent: list[tuple[str, object]] = []
        self._fail_on = fail_on
        self._calls = 0

    async def _send(self, kind, payload):
        self._calls += 1
        await asyncio.sleep(0)
        if self._fail_on is not None and self._calls >= self._fail_on:
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
    ws = RecordingWS(fail_on=1)  # first write fails: client is gone
    tts = AsyncMock()
    chunks = [b"1", b"2", b"3"]

    async def audio():
        for c in chunks:
            await asyncio.sleep(0)
            yield c

    tts_client = AsyncMock()
    tts_client.get_audio_stream = audio
    tts.connect = AsyncMock(return_value=tts_client)
    bridge, sender = _make(ws, output_mode=("text", "audio"), tts=tts)
    bridge.orchestrator.stream_response = _stream("a")

    await asyncio.wait_for(bridge._call_orchestrator("hola"), timeout=2.0)

    assert ws.sent == []  # nothing got through
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
