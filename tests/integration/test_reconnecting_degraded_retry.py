"""G-55 (#218): OpenClaw en DEGRADED se recupera solo con un reintento lento.

Tras `max_duration` el cliente entra en DEGRADED (circuit breaker #102: los probes no relanzan
la ventana completa). Ahora, además, un único reintento cada `max_backoff` s hasta que vuelva.
"""

import asyncio
from unittest.mock import AsyncMock

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.models import GatewayInfo
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.reconnection import ConnectionState

GATEWAY_INFO = GatewayInfo(
    default_agent_id="main",
        tick_interval_ms=30000,
    max_payload=26214400,
)


def _down_client():
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    return inner


async def _degraded(inner, *, max_backoff):
    roc = ReconnectingOpenClawClient(
        inner, "test", initial_backoff=0.01, max_backoff=max_backoff, max_duration=0.05
    )
    await roc.connect()
    for _ in range(100):
        if roc._reconnect_exhausted:
            break
        await asyncio.sleep(0.01)
    assert roc.state == ConnectionState.DEGRADED
    return roc


async def test_degraded_client_recovers_by_itself_when_openclaw_comes_back():
    inner = _down_client()
    roc = await _degraded(inner, max_backoff=0.05)
    states = []
    roc.on_state_change = states.append

    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO
    for _ in range(100):
        if roc.state == ConnectionState.CONNECTED:
            break
        await asyncio.sleep(0.02)

    assert roc.state == ConnectionState.CONNECTED
    assert roc._reconnect_exhausted is False
    assert roc.gateway_info is GATEWAY_INFO
    assert states == [ConnectionState.CONNECTED]  # una sola transición, sin aleteo
    await roc.close()


async def test_degraded_retry_is_slow_one_attempt_per_max_backoff():
    inner = _down_client()
    roc = await _degraded(inner, max_backoff=0.2)
    before = inner.connect.call_count

    await asyncio.sleep(0.9)  # ~4 ventanas de 0.2 s
    attempts = inner.connect.call_count - before

    assert 2 <= attempts <= 6, attempts
    assert roc.state == ConnectionState.DEGRADED  # sin aleteo de estado mientras falla
    await roc.close()


async def test_probes_in_degraded_do_not_add_attempts_or_tasks():
    inner = _down_client()
    roc = await _degraded(inner, max_backoff=60.0)
    calls = inner.connect.call_count
    task = roc._reconnect_task
    slow = roc._slow_retry_task

    for _ in range(200):
        await roc.ping()
        async for _event in roc.stream_response("x", session_key="agent:main:u"):
            pass

    assert inner.connect.call_count == calls
    assert roc._reconnect_task is task
    assert roc._slow_retry_task is slow
    await roc.close()


async def test_manual_trigger_still_gets_an_immediate_attempt_during_slow_retry():
    inner = _down_client()
    roc = await _degraded(inner, max_backoff=60.0)  # el reintento lento no vendría en 60 s
    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO

    roc.trigger_reconnect()
    await asyncio.sleep(0.1)

    assert roc.state == ConnectionState.CONNECTED
    assert roc._slow_retry_task is None or roc._slow_retry_task.done()
    await roc.close()


async def test_close_cancels_the_slow_retry():
    inner = _down_client()
    roc = await _degraded(inner, max_backoff=60.0)
    slow = roc._slow_retry_task
    assert slow is not None and not slow.done()

    await roc.close()

    assert slow.done()
