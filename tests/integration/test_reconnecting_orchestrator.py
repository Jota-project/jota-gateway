import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.services.orchestrators.protocol import OrchestratorEvent, OrchestratorProtocol
from src.services.orchestrators.reconnecting import (
    OrchestratorState,
    ReconnectingOrchestrator,
)


def _make_client(connect_side_effect=None):
    client = MagicMock(spec=OrchestratorProtocol)
    client.connect = AsyncMock(side_effect=connect_side_effect)
    client.close = AsyncMock()
    client.ping = AsyncMock(return_value=True)

    async def _stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="hi")

    client.stream_response = _stream
    return client


async def test_disconnect_triggers_reconnecting_state():
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.CONNECTED

    wrapper._handle_disconnect()

    assert wrapper._state == OrchestratorState.RECONNECTING
    if wrapper._reconnect_task:
        wrapper._reconnect_task.cancel()
        try:
            await wrapper._reconnect_task
        except asyncio.CancelledError:
            pass


async def test_stream_response_while_reconnecting_yields_error():
    client = _make_client()
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.RECONNECTING

    events = [e async for e in wrapper.stream_response("hello", "user-1")]

    assert len(events) == 1
    assert events[0].type == "error"
    assert events[0].content == "orchestrator_unavailable"
