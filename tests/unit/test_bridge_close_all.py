"""Tests for JotaBridge.close_all() idempotency (issue #101).

routes.py wraps connect_internal_services/health_check/ready-send/run() in a
single try/finally that unconditionally calls close_all() on exit. In the
normal-completion path, bridge.run() has *already* called close_all() from
its own internal finally before that outer finally runs — so close_all()
must tolerate being called twice without duplicating side effects, and the
first call's status must win.
"""
import asyncio

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


async def test_close_all_completes_teardown_after_earlier_call_is_interrupted(bridge):
    """The `_closed` guard used to be set *before* any teardown I/O ran, so if
    the first call was interrupted mid-teardown (e.g. task cancellation during
    app shutdown), every later call — including the routes.py outer-finally
    safety net that issue #101 added specifically to guarantee cleanup — became
    a silent no-op forever, and tracker.close() (which marks the session closed
    in SessionRegistry) never ran."""
    bridge._push_tts = AsyncMock()
    bridge._push_tts.close.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await bridge.close_all()

    bridge._push_tts = None
    await bridge.close_all()

    bridge.tracker._registry.close.assert_called_once()


async def test_late_close_of_old_bridge_keeps_reconnected_session(mock_tracker):
    """Issue #113 reconnect race, end to end through JotaBridge.close_all().

    A client disconnects and reconnects: the new session registers a second
    bridge under the SAME client_id, overwriting the old one in ClientRegistry.
    When the old bridge finally tears down (close_all → unregister) it must not
    evict the live new bridge — otherwise the reconnected client silently stops
    receiving push events and status broadcasts.
    """
    registry = ClientRegistry()

    def _make_bridge():
        return JotaBridge(
            client=_CLIENT, config=_CONFIG, client_ws=AsyncMock(),
            orchestrator=AsyncMock(), tts=AsyncMock(), tracker=mock_tracker,
            handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
            client_registry=registry, default_agent="main",
        )

    old_bridge = _make_bridge()
    new_bridge = _make_bridge()
    registry.register(old_bridge.client_id, old_bridge)
    registry.register(new_bridge.client_id, new_bridge)  # reconnect overwrites

    # Old bridge tears down late.
    await old_bridge.close_all()

    # The reconnected session survives...
    assert registry.get(new_bridge.client_id) is new_bridge

    # ...and still receives status broadcasts (old bridge's ws must not).
    await registry.broadcast_status("orchestrator", "restored")
    new_bridge.client_ws.send_json.assert_awaited_once_with(
        {"type": "status", "service": "orchestrator", "state": "restored"}
    )
    old_bridge.client_ws.send_json.assert_not_awaited()
