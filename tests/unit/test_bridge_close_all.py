"""Tests for JotaBridge.close_all() idempotency (issue #101).

routes.py wraps connect_internal_services/health_check/ready-send/run() in a
single try/finally that unconditionally calls close_all() on exit. In the
normal-completion path, bridge.run() has *already* called close_all() from
its own internal finally before that outer finally runs — so close_all()
must tolerate being called twice without duplicating side effects, and the
first call's status must win.
"""
import pytest
from unittest.mock import AsyncMock

from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


@pytest.fixture
def bridge(mock_tracker):
    ws = AsyncMock()
    registry = ClientRegistry()
    b = JotaBridge(
        client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(),
        tts=AsyncMock(), tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
        client_registry=registry, default_agent="main",
    )
    registry.register(b.client_id, b)
    return b


async def test_close_all_twice_does_not_raise(bridge):
    await bridge.close_all()
    await bridge.close_all()


async def test_close_all_twice_only_records_one_session_end_event(bridge):
    await bridge.close_all()
    await bridge.close_all()
    session_end_events = [e for e in bridge.tracker.events if e.stage == "session_end"]
    assert len(session_end_events) == 1


async def test_close_all_twice_only_calls_registry_close_once(bridge):
    await bridge.close_all()
    await bridge.close_all()
    bridge.tracker._registry.close.assert_called_once()


async def test_close_all_unregisters_from_client_registry(bridge):
    assert bridge._client_registry.get(bridge.client_id) is not None
    await bridge.close_all()
    assert bridge._client_registry.get(bridge.client_id) is None
    await bridge.close_all()  # no-op, must not raise
    assert bridge._client_registry.get(bridge.client_id) is None


async def test_close_all_default_status_is_completed(bridge):
    await bridge.close_all()
    call_args = bridge.tracker._registry.close.call_args
    assert call_args.args[1] == "completed"


async def test_close_all_status_error_passed_through(bridge):
    await bridge.close_all(status="error")
    call_args = bridge.tracker._registry.close.call_args
    assert call_args.args[1] == "error"


async def test_close_all_second_call_status_does_not_override_first(bridge):
    """First call (e.g. bridge.run()'s own cleanup, status='completed') wins —
    a later no-op call from routes.py's outer finally (status='error') must
    not flip an already-closed session's recorded status."""
    await bridge.close_all(status="completed")
    await bridge.close_all(status="error")
    call_args = bridge.tracker._registry.close.call_args
    assert call_args.args[1] == "completed"
