"""Un cancel (barge-in) justo después de `tts.connect()` no deja el stream huérfano."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from tests.unit.test_bridge_m6c_turns import _bridge


async def test_cancel_right_after_tts_connect_closes_the_stream():
    stream = MagicMock()
    stream.close = AsyncMock()
    tts = MagicMock()
    tts.name = "elevenlabs"
    tts.resolve_voice = MagicMock(return_value="v")
    tts.connect = AsyncMock(return_value=stream)
    bridge, _ws = _bridge(output_mode=("audio", "text"), tts=tts)
    in_record = asyncio.Event()

    async def blocking_record(name, **kw):
        if name == "tts_start":
            in_record.set()
            await asyncio.sleep(3600)

    bridge.tracker.record = blocking_record
    task = asyncio.create_task(bridge._run_turn("hola", "t-1", 1))
    await asyncio.wait_for(in_record.wait(), 2)
    task.cancel()
    await asyncio.wait({task}, timeout=2)
    assert task.cancelled()
    stream.close.assert_awaited_once()
