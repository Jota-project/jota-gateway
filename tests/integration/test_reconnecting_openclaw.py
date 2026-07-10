import asyncio
import pytest
from unittest.mock import AsyncMock
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.reconnection import ConnectionState
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.models import GatewayInfo
from src.services.protocol import OrchestratorEvent


def make_mock_client(gateway_info=None):
    client = AsyncMock(spec=OpenClawClient)
    client.gateway_info = gateway_info
    client.on_disconnect = None

    async def fake_connect():
        if gateway_info:
            client.gateway_info = gateway_info
        return gateway_info

    client.connect.side_effect = fake_connect

    async def fake_stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="hi")
        yield OrchestratorEvent(type="status", content="done")

    client.stream_response = fake_stream
    return client


GATEWAY_INFO = GatewayInfo(
    protocol_version=4, server_version="2026.6.6", conn_id="c1",
    default_agent_id="main", agents={}, tick_interval_ms=30000, max_payload=26214400,
)


@pytest.mark.asyncio
async def test_connect_sets_connected_state():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()
    assert roc.state == ConnectionState.CONNECTED
    assert roc.gateway_info is GATEWAY_INFO


@pytest.mark.asyncio
async def test_stream_response_delegates():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()
    events = []
    async for e in roc.stream_response("hello", "user", session_key="agent:main:u"):
        events.append(e)
    assert any(e.type == "token" for e in events)


@pytest.mark.asyncio
async def test_stream_response_when_not_connected_yields_error():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    # NOT calling connect() first
    events = []
    async for e in roc.stream_response("hello", "user", session_key="agent:main:u"):
        events.append(e)
    assert events[0].type == "error"
    assert "unavailable" in events[0].content


@pytest.mark.asyncio
async def test_disconnect_triggers_reconnect():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()
    inner.on_disconnect()  # simulate disconnect via the callback we assigned
    await asyncio.sleep(0.05)
    assert inner.connect.call_count >= 2  # initial + at least one retry


@pytest.mark.asyncio
async def test_reconnect_exhausted_enters_degraded():
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    roc = ReconnectingOpenClawClient(inner, "test", max_duration=0.1, initial_backoff=0.05)
    await roc.connect()  # this will fail → starts reconnect loop
    await asyncio.sleep(0.3)
    assert roc.state == ConnectionState.DEGRADED
