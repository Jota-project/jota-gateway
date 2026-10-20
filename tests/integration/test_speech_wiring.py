"""Los proveedores de voz se construyen en el lifespan y se inyectan (spec 2026-10-20, fase 1)."""

import httpx
import pytest

from src.core.config import settings
from src.services.reconnection import ConnectionState
from src.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider


def test_app_state_has_stt_and_tts_providers(client):
    state = client.app.state
    assert isinstance(state.stt, SpeechToTextProvider)
    assert isinstance(state.tts, TextToSpeechProvider)
    assert (state.stt.name, state.tts.name) == ("jota", "jota")


def test_ready_degraded_not_503_when_stt_and_tts_down(client, mock_services):
    """Foco 3: STT y TTS caídos siguen dando 200 degraded."""
    mock_services.get("http://localhost:8005/ready").mock(side_effect=httpx.ConnectError("down"))
    mock_services.get(f"http://{settings.TRANSCRIBER_WS_URL}/ready").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["services"]["transcriber"] == "unavailable"
    assert body["services"]["tts"] == "unavailable"
    assert body["services"]["orchestrator"] == "ok"


def test_stt_provider_state_change_broadcasts_transcriber_status(client):
    from unittest.mock import AsyncMock

    registry = client.app.state.client_registry
    registry.broadcast_status = AsyncMock()
    stt = client.app.state.stt

    async def fire():
        import asyncio

        stt.on_state_change(ConnectionState.DEGRADED)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    client.portal.call(fire)
    registry.broadcast_status.assert_awaited_once_with("transcriber", "unavailable")


@pytest.fixture
def failing_stt_start(monkeypatch):
    from src.services.speech.jota import JotaSttProvider

    async def boom(self):
        raise RuntimeError("secret-provider-detail")

    monkeypatch.setattr(JotaSttProvider, "start", boom)


def test_provider_start_failure_does_not_abort_startup(failing_stt_start, client, caplog):
    """Solo el orquestador es fatal: un start() de proveedor que falla degrada (#110)."""
    assert client.get("/healthz").status_code == 200
    assert "secret-provider-detail" not in caplog.text


def test_admin_status_includes_provider(client):
    h = {"X-Admin-Token": "test-admin-token"}
    assert client.get("/admin/transcriber/status", headers=h).json()["provider"] == "jota"
    assert client.get("/admin/tts/status", headers=h).json()["provider"] == "jota"
