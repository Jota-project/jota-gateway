"""G-02 (#214): the normal turn's TTS is bounded like the push one (#199)."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import websockets

from src.core.config import settings
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.protocol import OrchestratorEvent
from src.services.tts_client import TTSClient


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, p):
        self.sent.append(p)

    async def send_bytes(self, b):
        self.sent.append(b)


def _stream(*tokens):
    async def _s(*a, **k):
        for t in tokens:
            yield OrchestratorEvent(type="token", content=t)

    return _s


def _bridge(ws, tts_client):
    tts = AsyncMock()
    tts.connect = AsyncMock(return_value=tts_client)
    b = JotaBridge(
        client=Client(id="u", client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=tts,
        tracker=AsyncMock(),
        handshake=Handshake(client_key="k", input_mode="text", output_mode=["text", "audio"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    b.transcriber = None
    return b


async def _run_bounded(bridge, timeout):
    """Run the turn; FAIL (not hang) if it doesn't finish in `timeout` (no fix)."""
    task = asyncio.create_task(bridge._call_orchestrator("x"))
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        await asyncio.wait({task}, timeout=0.5)
        raise AssertionError(f"el turno no terminó en {timeout}s")
    await task


async def test_alive_tts_that_never_sends_done_ends_turn_by_cap(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.2)
    server_closed = asyncio.Event()

    async def handler(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.send(b"\x01\x02")  # some audio, but never a `done`
        try:
            async for _ in ws:
                pass
        finally:
            server_closed.set()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        tts_client = TTSClient(url=f"127.0.0.1:{port}", token="t", client_id="c1")
        await tts_client.connect()
        ws = _WS()
        bridge = _bridge(ws, tts_client)
        bridge.orchestrator.stream_response = _stream("hola")

        with caplog.at_level(logging.WARNING):
            await _run_bounded(bridge, 3.0)

        assert ws.sent[-1] == {"type": "turn_end", "turn_id": "t-1"}
        await asyncio.wait_for(server_closed.wait(), timeout=2.0)
    assert any("TTS" in r.getMessage() for r in caplog.records)


async def test_hung_send_text_chunk_and_end_do_not_wedge_turn(monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.1)

    async def hang(*a, **k):
        await asyncio.sleep(3600)

    async def audio():
        await asyncio.sleep(3600)
        yield b""

    tts_client = AsyncMock()
    tts_client.send_text_chunk = hang
    tts_client.end = hang
    tts_client.close = hang
    tts_client.get_audio_stream = audio
    ws = _WS()
    bridge = _bridge(ws, tts_client)
    bridge.orchestrator.stream_response = _stream("a", "b")

    await _run_bounded(bridge, 3.0)

    assert ws.sent[-1] == {"type": "turn_end", "turn_id": "t-1"}
    # text tokens still reached the client despite the dead TTS
    assert [m["text"] for m in ws.sent if isinstance(m, dict) and m["type"] == "token"] == [
        "a",
        "b",
    ]


async def test_healthy_slow_tts_is_not_truncated_by_idle_cap(monkeypatch):
    """The cap after the last token is per inactivity: a TTS that keeps emitting
    chunks for longer than the cap (no pause > cap) must be delivered in full."""
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.5)
    total = 20

    async def audio():
        for i in range(total):
            await asyncio.sleep(0.1)
            yield bytes([i])

    tts_client = AsyncMock()
    tts_client.get_audio_stream = audio
    ws = _WS()
    bridge = _bridge(ws, tts_client)
    bridge.orchestrator.stream_response = _stream("a")

    await _run_bounded(bridge, 5.0)

    chunks = [m for m in ws.sent if isinstance(m, bytes)]
    assert len(chunks) == total
    assert ws.sent[-1] == {"type": "turn_end", "turn_id": "t-1"}


async def _cancel_turn_during(monkeypatch, *, tokens_block):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 5.0)

    async def audio():
        await asyncio.sleep(3600)
        yield b""

    tts_client = AsyncMock()
    tts_client.get_audio_stream = audio
    ws = _WS()
    bridge = _bridge(ws, tts_client)
    entered = asyncio.Event()

    async def stream(*a, **k):
        if tokens_block:
            entered.set()
            await asyncio.sleep(3600)
        yield OrchestratorEvent(type="token", content="a")

    bridge.orchestrator.stream_response = stream
    before = asyncio.all_tasks()
    task = asyncio.create_task(bridge._call_orchestrator("x"))
    if tokens_block:
        await asyncio.wait_for(entered.wait(), 2.0)
    else:
        await asyncio.sleep(0.2)  # tokens done, now waiting for audio
    task.cancel()
    try:
        await asyncio.wait_for(task, 2.0)
    except asyncio.CancelledError:
        pass
    tts_client.close.assert_awaited()
    await asyncio.sleep(0)
    leftover = [t for t in asyncio.all_tasks() - before if not t.done() and t is not task]
    assert leftover == []


async def test_barge_in_cancel_during_tokens_closes_tts_no_orphans(monkeypatch):
    await _cancel_turn_during(monkeypatch, tokens_block=True)


async def test_barge_in_cancel_during_audio_wait_closes_tts_no_orphans(monkeypatch):
    await _cancel_turn_during(monkeypatch, tokens_block=False)
