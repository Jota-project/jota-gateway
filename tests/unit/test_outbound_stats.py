"""#219: profundidad y máximos de la cola de salida."""

import asyncio

import pytest

from src.services.outbound import DirectSender, QueuedSender


class BlockingWS:
    def __init__(self):
        self.gate = asyncio.Event()

    async def send_json(self, payload):
        await self.gate.wait()

    async def send_bytes(self, data):
        await self.gate.wait()


class FailAfterGateWS:
    """Blocks the first send until released, then fails it."""

    def __init__(self):
        self.gate = asyncio.Event()

    async def send_json(self, payload):
        await self.gate.wait()
        raise OSError("boom")

    send_bytes = send_json


@pytest.mark.asyncio
async def test_queued_sender_tracks_pending_and_peak_bytes():
    ws = BlockingWS()
    sender = QueuedSender(ws)
    await sender.send_bytes(b"x" * 100)
    await sender.send_bytes(b"x" * 100)
    await sender.send_json({})
    stats = sender.queue_stats()
    assert stats["pending_msgs"] >= 2
    assert stats["peak_bytes"] >= 100
    ws.gate.set()
    await sender.flush()
    stats = sender.queue_stats()
    assert stats["pending_msgs"] == 0
    assert stats["pending_bytes"] == 0
    assert stats["peak_bytes"] >= 100
    await sender.aclose(1.0)


@pytest.mark.asyncio
async def test_discard_pending_resets_pending():
    ws = FailAfterGateWS()
    sender = QueuedSender(ws)
    await sender.send_json({})  # the writer takes it and blocks on the gate
    await asyncio.sleep(0)
    await sender.send_bytes(b"x" * 10)
    await sender.send_bytes(b"x" * 10)
    await sender.send_json({})
    stats = sender.queue_stats()
    assert stats["pending_msgs"] == 3 and stats["pending_bytes"] == 20
    ws.gate.set()
    await asyncio.wait_for(sender._writer, 1.0)
    stats = sender.queue_stats()
    assert stats["pending_msgs"] == 0 and stats["pending_bytes"] == 0
    assert stats["peak_msgs"] == 3
    await sender.aclose(1.0)


def test_direct_sender_stats_zero():
    assert DirectSender(None).queue_stats() == {
        "pending_msgs": 0, "pending_bytes": 0, "peak_msgs": 0, "peak_bytes": 0,
    }
