"""Issue #110 / #221: lifespan shutdown unhooks the state-change broadcasts, closes
the speech providers and OpenClaw, cancels pending notification tasks and disposes
the DB engine, even if a step fails. Sessions are not drained here any more: uvicorn
closes every WebSocket (1012) and waits for its handler before the lifespan shutdown
(the end-to-end shutdown with an open session is in tests/integration/harness/).
"""

from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from fogar_gateway.main import app
from tests.integration.conftest import _configure_app_mocks


def test_lifespan_shutdown_unhooks_broadcasts_closes_openclaw_and_disposes_engine(
    mock_services, mock_registry, seed_client, monkeypatch
):
    """G-68: the state-change hooks are disconnected before the services close, so a
    service closing during shutdown cannot schedule a notification task that outlives
    the loop. The lifespan no longer drains sessions (#221): uvicorn already closed
    them (1012) before the lifespan shutdown runs."""
    _configure_app_mocks(mock_registry, monkeypatch)
    dispose_mock = MagicMock()
    monkeypatch.setattr("fogar_gateway.main.dispose_engine", dispose_mock)
    hooks_at_close = {}

    async def _record_hooks(*args, **kwargs):
        hooks_at_close["openclaw"] = app.state.openclaw.on_state_change
        hooks_at_close["stt"] = app.state.speech.stt.on_state_change
        hooks_at_close["tts"] = app.state.speech.tts.on_state_change

    mock_registry.close = AsyncMock(side_effect=_record_hooks)

    with TestClient(app, client=("127.0.0.1", 50000)):
        assert app.state.openclaw.on_state_change is not None
        assert app.state.speech.stt.on_state_change is not None

    mock_registry.close.assert_awaited_once()
    assert hooks_at_close == {"openclaw": None, "stt": None, "tts": None}
    dispose_mock.assert_called_once()


def test_lifespan_shutdown_disposes_engine_even_if_openclaw_close_raises(
    mock_services, mock_registry, seed_client, monkeypatch
):
    _configure_app_mocks(mock_registry, monkeypatch)
    mock_registry.close = AsyncMock(side_effect=RuntimeError("openclaw close boom"))
    dispose_mock = MagicMock()
    monkeypatch.setattr("fogar_gateway.main.dispose_engine", dispose_mock)

    with TestClient(app, client=("127.0.0.1", 50000)):
        pass  # no active bridges — exercises the openclaw.close()-raises path directly

    dispose_mock.assert_called_once()
