import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.services.protocol import OrchestratorEvent, OrchestratorProtocol
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


async def test_reconnect_success_restores_connected_state():
    client = _make_client()  # connect() succeeds immediately
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.RECONNECTING
    wrapper._reconnect_task = asyncio.create_task(wrapper._reconnect_loop())

    await wrapper._reconnect_task

    assert wrapper._state == OrchestratorState.CONNECTED
    assert wrapper._reconnect_attempts == 0
    assert wrapper._connected_at is not None


async def test_reconnect_exhausted_goes_degraded(monkeypatch):
    monkeypatch.setattr(
        "src.services.orchestrators.reconnecting.settings",
        type("S", (), {
            "ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF": 0.0,
            "ORCHESTRATOR_RECONNECT_MAX_BACKOFF": 0.0,
            "ORCHESTRATOR_RECONNECT_MAX_DURATION": 0.0,
        })(),
    )
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.RECONNECTING
    wrapper._reconnect_task = asyncio.create_task(wrapper._reconnect_loop())

    await wrapper._reconnect_task

    assert wrapper._state == OrchestratorState.DEGRADED
    assert wrapper._reconnect_attempts == 1
    assert wrapper._last_error == "refused"


async def test_lazy_reconnect_on_stream_in_degraded():
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.DEGRADED

    events = [e async for e in wrapper.stream_response("hello", "user-1")]

    assert events[0].type == "error"
    assert events[0].content == "orchestrator_unavailable"
    assert wrapper._state == OrchestratorState.RECONNECTING
    assert wrapper._reconnect_task is not None
    wrapper._reconnect_task.cancel()
    try:
        await wrapper._reconnect_task
    except asyncio.CancelledError:
        pass


async def test_manual_trigger_reconnect():
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.DEGRADED

    await wrapper.trigger_reconnect()

    assert wrapper._state == OrchestratorState.RECONNECTING
    assert wrapper._reconnect_task is not None
    wrapper._reconnect_task.cancel()
    try:
        await wrapper._reconnect_task
    except asyncio.CancelledError:
        pass


async def test_degraded_does_not_restart_reconnect_loop_on_ping(monkeypatch):
    """En estado DEGRADED (loop agotado), ping() NO debe lanzar un nuevo _reconnect_loop.

    Bug 10: _ensure_reconnecting() comprueba _reconnect_task.done(), que es True tras
    agotar el loop. Sin flag de exhausted, cada ping en DEGRADED lanza un loop nuevo,
    dando otra ventana completa de reintentos y oscilando indefinidamente.
    """
    monkeypatch.setattr(
        "src.services.orchestrators.reconnecting.settings",
        type("S", (), {
            "ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF": 0.0,
            "ORCHESTRATOR_RECONNECT_MAX_BACKOFF": 0.0,
            "ORCHESTRATOR_RECONNECT_MAX_DURATION": 0.0,
        })(),
    )
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.RECONNECTING
    wrapper._reconnect_task = asyncio.create_task(wrapper._reconnect_loop())
    await wrapper._reconnect_task  # agota el loop → DEGRADED

    assert wrapper._state == OrchestratorState.DEGRADED
    task_after_exhaustion = wrapper._reconnect_task

    # Ahora ping() — en DEGRADED debería NO lanzar un nuevo loop
    result = await wrapper.ping()
    assert result is False  # sigue DEGRADED

    # El reconnect_task NO debe haber cambiado
    assert wrapper._reconnect_task is task_after_exhaustion, (
        "ping() en DEGRADED (loop agotado) no debe lanzar un nuevo _reconnect_loop — "
        "solo trigger_reconnect() puede reiniciar el circuit-breaker"
    )
    assert wrapper._state == OrchestratorState.DEGRADED


async def test_trigger_reconnect_resets_exhausted_flag(monkeypatch):
    """trigger_reconnect() debe permitir un nuevo ciclo de reconexión aunque el loop esté agotado."""
    monkeypatch.setattr(
        "src.services.orchestrators.reconnecting.settings",
        type("S", (), {
            "ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF": 0.0,
            "ORCHESTRATOR_RECONNECT_MAX_BACKOFF": 0.0,
            "ORCHESTRATOR_RECONNECT_MAX_DURATION": 0.0,
        })(),
    )
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.RECONNECTING
    wrapper._reconnect_task = asyncio.create_task(wrapper._reconnect_loop())
    await wrapper._reconnect_task  # agota → DEGRADED

    task_first = wrapper._reconnect_task

    # trigger_reconnect() explícito debe iniciar un nuevo loop
    await wrapper.trigger_reconnect()
    await wrapper._reconnect_task  # dejar que el nuevo loop también agote

    assert wrapper._reconnect_task is not task_first, (
        "trigger_reconnect() debe crear un nuevo _reconnect_task"
    )


async def test_stream_response_wraps_inner_exception_as_error_event():
    """Si _client.stream_response lanza mid-stream, debe yieldearse un error event en lugar de propagar."""
    client = _make_client()
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.CONNECTED

    async def _crashing_stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="parcial")
        raise OSError("connection reset by peer")

    client.stream_response = _crashing_stream

    events = [e async for e in wrapper.stream_response("hello", "user-1", session_key="k")]

    error_events = [e for e in events if e.type == "error"]
    assert error_events, "Debe haber al menos un evento de error cuando el stream interno lanza"
    assert "connection reset" in error_events[0].content.lower() or "reset" in error_events[0].content.lower()


async def test_stream_response_propagates_cancelled_error():
    """CancelledError debe propagarse sin convertirse en error event."""
    client = _make_client()
    wrapper = ReconnectingOrchestrator(client, name="test")
    wrapper._state = OrchestratorState.CONNECTED

    async def _cancelled_stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="tok")
        raise asyncio.CancelledError()

    client.stream_response = _cancelled_stream

    import pytest
    with pytest.raises(asyncio.CancelledError):
        async for _ in wrapper.stream_response("hello", "user-1", session_key="k"):
            pass


async def test_status_fields():
    client = _make_client(connect_side_effect=OSError("refused"))
    wrapper = ReconnectingOrchestrator(client, name="myorch")
    wrapper._state = OrchestratorState.CONNECTED
    wrapper._connected_at = datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)

    wrapper._handle_disconnect()
    if wrapper._reconnect_task:
        wrapper._reconnect_task.cancel()
        try:
            await wrapper._reconnect_task
        except asyncio.CancelledError:
            pass

    s = wrapper.status()

    assert s.name == "myorch"
    assert s.state == OrchestratorState.RECONNECTING
    assert s.connected_at == datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)
    assert isinstance(s.disconnected_at, datetime)
    assert s.reconnect_attempts == 0
