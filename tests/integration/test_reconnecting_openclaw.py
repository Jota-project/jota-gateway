import asyncio
import contextlib
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


@pytest.mark.asyncio
async def test_circuit_breaker_stays_open_after_exhaustion():
    """Issue #102: once the reconnect loop exhausts max_duration and enters
    DEGRADED, further probes (ping()) must not relaunch a fresh 300s reconnect
    window — the circuit stays open until an explicit trigger_reconnect() or
    a successful connect()."""
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    roc = ReconnectingOpenClawClient(inner, "test", max_duration=0.1, initial_backoff=0.05)
    await roc.connect()  # fails → starts reconnect loop
    await asyncio.sleep(0.3)
    assert roc.state == ConnectionState.DEGRADED

    calls_at_degraded = inner.connect.call_count
    exhausted_task = roc._reconnect_task

    for _ in range(100):
        await roc.ping()

    assert roc.state == ConnectionState.DEGRADED
    assert inner.connect.call_count == calls_at_degraded
    assert roc._reconnect_task is exhausted_task


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_manual_reconnect():
    """Issue #102: an admin-triggered trigger_reconnect() must explicitly
    reset _reconnect_exhausted, even though the circuit is open, so the
    admin-facing reconnect endpoint always gets a fresh attempt."""
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    roc = ReconnectingOpenClawClient(inner, "test", max_duration=0.1, initial_backoff=0.05)
    await roc.connect()  # fails → starts reconnect loop
    await asyncio.sleep(0.3)
    assert roc.state == ConnectionState.DEGRADED
    calls_before = inner.connect.call_count

    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO

    job_id = roc.trigger_reconnect()

    assert isinstance(job_id, str) and job_id
    assert roc.state == ConnectionState.RECONNECTING
    assert roc._reconnect_exhausted is False
    await asyncio.sleep(0.05)
    assert roc.state == ConnectionState.CONNECTED
    assert inner.connect.call_count > calls_before


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_implicit_connect():
    """Issue #102: a direct, successful connect() call (e.g. a caller that
    retries connect() itself after observing DEGRADED) must also clear
    _reconnect_exhausted — not just the admin-triggered path."""
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    roc = ReconnectingOpenClawClient(inner, "test", max_duration=0.1, initial_backoff=0.05)
    await roc.connect()  # fails → starts reconnect loop
    await asyncio.sleep(0.3)
    assert roc.state == ConnectionState.DEGRADED
    assert roc._reconnect_exhausted is True

    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO

    await roc.connect()

    assert roc.state == ConnectionState.CONNECTED
    assert roc._reconnect_exhausted is False


@pytest.mark.asyncio
async def test_trigger_reconnect_returns_job_id_and_reconnects():
    """Issue #103: the admin reconnect endpoint must not block on a full
    connect() — trigger_reconnect() hands back a job id immediately while the
    actual reconnect runs in the background reconnect loop."""
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")

    job_id = roc.trigger_reconnect()

    assert isinstance(job_id, str) and job_id
    assert roc.state == ConnectionState.RECONNECTING
    await asyncio.sleep(0.05)
    assert roc.state == ConnectionState.CONNECTED
    assert inner.connect.call_count == 1


@pytest.mark.asyncio
async def test_reconnect_loop_success_clears_last_error():
    """Issue #104: a successful reconnect via the background _reconnect_loop()
    (not the initial connect()) must clear _last_error — otherwise status()
    keeps reporting a stale error from before the drop even though the
    service is healthy again right now."""
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = [RuntimeError("refused"), GATEWAY_INFO]

    roc = ReconnectingOpenClawClient(inner, "test", max_duration=5.0, initial_backoff=0.01)

    await roc._reconnect_loop()

    assert roc.state == ConnectionState.CONNECTED
    assert roc.status().last_error is None


@pytest.mark.asyncio
async def test_trigger_reconnect_coalesces_with_in_flight_reconnect():
    """An admin-triggered reconnect racing the background reconnect loop
    (already in flight, e.g. after an unexpected disconnect) must coalesce
    onto the same job — not spawn a second concurrent connect() attempt."""
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()

    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None

    async def slow_connect():
        connect_started.set()
        await release_connect.wait()
        return GATEWAY_INFO

    inner.connect.side_effect = slow_connect

    roc = ReconnectingOpenClawClient(inner, "test")
    first_job_id = roc.trigger_reconnect()
    await connect_started.wait()  # background loop is now mid-attempt

    second_job_id = roc.trigger_reconnect()

    assert second_job_id == first_job_id
    assert inner.connect.call_count == 1  # no duplicate attempt spawned

    release_connect.set()
    await asyncio.sleep(0.05)
    assert roc.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_stream_response_closes_inner_generator_on_early_exit():
    """Issue #150: in production, `call_orchestrator()` (src/services/orchestration.py)
    wraps `ReconnectingOpenClawClient.stream_response()` in `contextlib.aclosing()`
    and raises on an `error` event, closing the OUTER generator early. The INNER
    generator — the real `OpenClawClient.stream_response()`, which holds
    `finally: self._turn_registry.unregister(...)` — must be closed synchronously
    at that same moment, not abandoned for the asyncgen GC finalizer to eventually
    get around to (which would leave the session_key locked with `TurnInProgress`)."""
    inner_finally_ran = []

    async def fake_stream(*args, **kwargs):
        try:
            yield OrchestratorEvent(type="error", content="boom")
            yield OrchestratorEvent(type="token", content="never reached")
        finally:
            inner_finally_ran.append(True)

    inner = make_mock_client(GATEWAY_INFO)
    inner.stream_response = fake_stream
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()

    with pytest.raises(RuntimeError):
        async with contextlib.aclosing(
            roc.stream_response("hi", "user", session_key="agent:main:u")
        ) as stream:
            async for event in stream:
                if event.type == "error":
                    # Mirrors call_orchestrator() raising on an error event.
                    raise RuntimeError(event.content)

    assert inner_finally_ran, (
        "inner OpenClawClient generator's finally (TurnRegistry.unregister) "
        "never ran synchronously — it was abandoned instead of closed"
    )
