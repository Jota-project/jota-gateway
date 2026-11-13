import asyncio
import contextlib
import itertools
from unittest.mock import AsyncMock

import pytest

from fogar_gateway.services.openclaw.client import OpenClawClient
from fogar_gateway.services.openclaw.models import GatewayInfo
from fogar_gateway.services.openclaw.reconnecting import ReconnectingOpenClawClient
from fogar_gateway.services.protocol import OrchestratorEvent
from fogar_gateway.services.reconnection import ConnectionState


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
    default_agent_id="main",
        tick_interval_ms=30000,
    max_payload=26214400,
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
    async for e in roc.stream_response("hello", session_key="agent:main:u"):
        events.append(e)
    assert any(e.type == "token" for e in events)


@pytest.mark.asyncio
async def test_stream_response_when_not_connected_yields_error():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    # NOT calling connect() first
    events = []
    async for e in roc.stream_response("hello", session_key="agent:main:u"):
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


def _down_inner():
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    return inner


@pytest.mark.asyncio
async def test_outage_never_exhausts_and_stays_reconnecting():
    """#272: one endless loop — no exhaustion, no DEGRADED, no second retry layer."""
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01, max_backoff=0.02)
    await roc.connect()  # fails -> starts the loop
    await asyncio.sleep(0.3)
    assert roc.state == ConnectionState.RECONNECTING
    assert inner.connect.call_count > 5
    assert not roc._reconnect_task.done()
    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO
    await asyncio.sleep(0.1)
    assert roc.state == ConnectionState.CONNECTED
    await roc.close()


@pytest.mark.asyncio
async def test_backoff_doubles_and_is_capped():
    inner = _down_inner()
    stamps: list[float] = []
    loop = asyncio.get_running_loop()

    async def failing():
        stamps.append(loop.time())
        raise RuntimeError("refused")

    inner.connect.side_effect = failing
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.05, max_backoff=0.1)
    roc._ensure_loop()
    await asyncio.sleep(0.65)
    await roc.close()

    gaps = [b - a for a, b in itertools.pairwise(stamps)]
    assert len(gaps) >= 5, gaps
    assert 0.04 <= gaps[0] < 0.08  # initial
    assert 0.09 <= gaps[1] < 0.14  # doubled = cap
    assert all(g < 0.16 for g in gaps[2:]), gaps  # never above the cap


@pytest.mark.asyncio
async def test_probes_while_reconnecting_do_not_add_attempts_or_tasks():
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=60.0, max_backoff=60.0)
    await roc.connect()
    await asyncio.sleep(0.01)
    calls = inner.connect.call_count
    task = roc._reconnect_task

    for _ in range(100):
        await roc.ping()
        async for _event in roc.stream_response("x", session_key="agent:main:u"):
            pass
    await asyncio.sleep(0.01)

    assert inner.connect.call_count == calls
    assert roc._reconnect_task is task
    await roc.close()


@pytest.mark.asyncio
async def test_trigger_reconnect_cancels_the_wait_and_retries_now():
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=60.0, max_backoff=60.0)
    await roc.connect()  # fails; the loop now waits 60 s
    await asyncio.sleep(0.01)
    calls = inner.connect.call_count
    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO

    assert roc.trigger_reconnect() is None
    await asyncio.sleep(0.05)

    assert roc.state == ConnectionState.CONNECTED
    assert inner.connect.call_count == calls + 1
    await roc.close()


@pytest.mark.asyncio
async def test_trigger_reconnect_resets_backoff():
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01, max_backoff=60.0)
    roc._ensure_loop()
    await asyncio.sleep(0.2)  # backoff has grown to ~0.16 s+; next wait is long
    calls = inner.connect.call_count
    roc.trigger_reconnect()
    await asyncio.sleep(0.03)
    assert inner.connect.call_count > calls  # retried right away
    await roc.close()


@pytest.mark.asyncio
async def test_any_connect_failure_is_one_more_failed_attempt():
    """A future «pairing required» error from connect() is just another failure."""

    class PairingRequired(Exception):
        stable_code = "pairing_required"

    inner = _down_inner()
    inner.connect.side_effect = [PairingRequired("secret text"), OSError("x"), GATEWAY_INFO]
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01)
    await roc.connect()
    assert roc.status().last_error == "pairing_required"
    await asyncio.sleep(0.1)
    assert roc.state == ConnectionState.CONNECTED
    assert roc.status().last_error is None
    await roc.close()


@pytest.mark.asyncio
async def test_close_stops_the_loop_for_good():
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01, max_backoff=0.02)
    await roc.connect()
    task = roc._reconnect_task
    await roc.close()
    assert task.done()
    calls = inner.connect.call_count
    roc.trigger_reconnect()
    await asyncio.sleep(0.1)
    assert inner.connect.call_count == calls


@pytest.mark.asyncio
async def test_close_cancels_a_long_backoff_wait_quickly():
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=60.0, max_backoff=60.0)
    await roc.connect()
    await asyncio.sleep(0.01)
    async with asyncio.timeout(1):
        await roc.close()
    assert roc._reconnect_task.done()


@pytest.mark.asyncio
async def test_direct_connect_success_stops_the_loop():
    inner = _down_inner()
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01, max_backoff=0.02)
    await roc.connect()
    inner.connect.side_effect = None
    inner.connect.return_value = GATEWAY_INFO
    await roc.connect()
    assert roc.state == ConnectionState.CONNECTED
    await asyncio.sleep(0.1)
    assert roc._reconnect_task.done()
    await roc.close()


@pytest.mark.asyncio
async def test_trigger_reconnect_connects_without_blocking():
    """Issue #103: trigger_reconnect() returns immediately; the connect runs in the loop."""
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")

    roc.trigger_reconnect()

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

    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01)

    await roc._reconnect_loop()

    assert roc.state == ConnectionState.CONNECTED
    assert roc.status().last_error is None


@pytest.mark.asyncio
async def test_trigger_reconnect_coalesces_with_in_flight_attempt():
    """A trigger racing an attempt already in flight must not spawn a second connect()."""
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
    roc.trigger_reconnect()
    await connect_started.wait()  # background loop is now mid-attempt

    roc.trigger_reconnect()

    assert inner.connect.call_count == 1  # no duplicate attempt spawned

    release_connect.set()
    await asyncio.sleep(0.05)
    assert roc.state == ConnectionState.CONNECTED
    assert inner.connect.call_count == 1


@pytest.mark.asyncio
async def test_stream_response_closes_inner_generator_on_early_exit():
    """Issue #150: in production, `call_orchestrator()` (src/fogar_gateway/services/orchestration.py)
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
            roc.stream_response("hi", session_key="agent:main:u")
        ) as stream:
            async for event in stream:
                if event.type == "error":
                    # Mirrors call_orchestrator() raising on an error event.
                    raise RuntimeError(event.content)

    assert inner_finally_ran, (
        "inner OpenClawClient generator's finally (TurnRegistry.unregister) "
        "never ran synchronously — it was abandoned instead of closed"
    )


class _SlowToStopWS:
    """Socket del que el listener tarda en soltar al cancelarlo (cierre en curso)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            raise

    async def close(self):
        pass


async def test_close_does_not_hang_when_cancel_lands_inside_connect(monkeypatch):
    """G-52 (#221): si la cancelación de close() cae dentro del connect() de la reconexión
    (mientras desmonta el listener anterior), no se pierde. Antes el listener se tragaba
    su cancelación, el bucle seguía reintentando y close() esperaba hasta max_duration."""
    from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
    from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry

    turns = TurnRegistry()
    inner = OpenClawClient("127.0.0.1", 1, "t", turns, FrameDispatcher(turns, ClientRegistry()))
    inner._ws = _SlowToStopWS()
    inner._listener_task = asyncio.create_task(inner._listen())
    await asyncio.sleep(0)

    async def refused(*_a, **_k):
        raise OSError("refused")

    monkeypatch.setattr("websockets.connect", refused)
    roc = ReconnectingOpenClawClient(inner, "test", initial_backoff=0.01)
    roc._ensure_loop()
    await asyncio.sleep(0.01)  # la reconexión está en connect() → _cancel_and_close()

    closer = asyncio.create_task(roc.close())
    await asyncio.wait({closer}, timeout=1.0)
    try:
        assert closer.done(), "close() se quedó esperando a la reconexión"
        assert roc._reconnect_task.done()
    finally:
        roc._reconnect_task.cancel()
        await asyncio.wait({closer}, timeout=1.0)


async def test_after_close_nothing_starts_a_reconnect():
    """G-52 (#221): tras close(), ni ping(), ni stream_response(), ni una caída avisada
    por el cliente interno vuelven a crear la tarea de reconexión."""
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")  # sin connect(): DEGRADED
    await roc.close()

    assert await roc.ping() is False
    events = [e async for e in roc.stream_response("hola", session_key="agent:main:u")]
    inner.on_disconnect()
    roc.trigger_reconnect()
    await asyncio.sleep(0.01)

    assert events[0].content == "orchestrator_unavailable"
    assert roc._reconnect_task is None
    inner.connect.assert_not_called()


async def test_close_does_not_swallow_callers_cancellation():
    """G-52 (#221): si cancelan a quien llama a close() mientras espera a la reconexión,
    el cierre termina y la cancelación sale (antes `except CancelledError: pass`)."""
    inner = make_mock_client(GATEWAY_INFO)
    connecting = asyncio.Event()

    async def slow_connect():
        connecting.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)  # tarda en soltar: la cancelación del closer llega aquí
            raise

    inner.connect.side_effect = slow_connect
    roc = ReconnectingOpenClawClient(inner, "test")
    roc._ensure_loop()
    await connecting.wait()

    closer = asyncio.create_task(roc.close())
    await asyncio.sleep(0.01)
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer
    assert roc._reconnect_task.done()
    inner.close.assert_awaited_once()
