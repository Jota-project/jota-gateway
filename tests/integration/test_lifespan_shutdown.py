"""Issue #110: lifespan shutdown must drain active bridges, cancel/await
notification tasks, close OpenClaw only after sessions have drained, and
dispose the DB engine — instead of relying on Uvicorn's external
cancellation to clean any of this up.

Uses a bridge registered directly into app.state.client_registry (rather
than a real, still-open WebSocket session) so the test is deterministic and
cannot hang the suite if the fix regresses — see the plan doc for why a
real open-WebSocket-across-shutdown spike was rejected (it reproducibly
hung the *pre-fix* code indefinitely, since nothing cancelled the blocked
receive() loop).
"""

from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from src.main import app
from tests.integration.conftest import _configure_app_mocks


def test_lifespan_shutdown_drains_bridges_closes_openclaw_and_disposes_engine(
    mock_services, mock_registry, seed_client, monkeypatch
):
    _configure_app_mocks(mock_registry, monkeypatch)
    dispose_mock = MagicMock()
    monkeypatch.setattr("src.main.dispose_engine", dispose_mock)
    openclaw_close_count_during_drain = None

    async def _record_openclaw_close_count(*args, **kwargs):
        nonlocal openclaw_close_count_during_drain
        openclaw_close_count_during_drain = mock_registry.close.await_count

    with TestClient(app, client=("127.0.0.1", 50000)):
        fake_bridge = AsyncMock()
        fake_bridge.close_all.side_effect = _record_openclaw_close_count
        app.state.client_registry.register("fake-client", fake_bridge)
        # __exit__ below triggers the real ASGI lifespan shutdown sequence.

    fake_bridge.close_all.assert_awaited_once_with(status="shutdown")
    mock_registry.close.assert_awaited_once()
    dispose_mock.assert_called_once()
    assert openclaw_close_count_during_drain == 0, (
        "OpenClaw was closed before (or during) the bridge drain — "
        "sessions must finish draining before OpenClaw closes"
    )


def test_lifespan_shutdown_closes_openclaw_even_if_a_bridge_fails_to_drain(
    mock_services, mock_registry, seed_client, monkeypatch
):
    """One session that errors or times out on shutdown must not prevent
    OpenClaw from closing or the DB engine from being disposed."""
    _configure_app_mocks(mock_registry, monkeypatch)
    dispose_mock = MagicMock()
    monkeypatch.setattr("src.main.dispose_engine", dispose_mock)

    with TestClient(app, client=("127.0.0.1", 50000)):
        broken_bridge = AsyncMock()
        broken_bridge.close_all.side_effect = RuntimeError("boom")
        app.state.client_registry.register("broken-client", broken_bridge)

    mock_registry.close.assert_awaited_once()
    dispose_mock.assert_called_once()


def test_lifespan_shutdown_disposes_engine_even_if_openclaw_close_raises(
    mock_services, mock_registry, seed_client, monkeypatch
):
    _configure_app_mocks(mock_registry, monkeypatch)
    mock_registry.close = AsyncMock(side_effect=RuntimeError("openclaw close boom"))
    dispose_mock = MagicMock()
    monkeypatch.setattr("src.main.dispose_engine", dispose_mock)

    with TestClient(app, client=("127.0.0.1", 50000)):
        pass  # no active bridges — exercises the openclaw.close()-raises path directly

    dispose_mock.assert_called_once()
