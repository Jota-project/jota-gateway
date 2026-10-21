"""Los proveedores de voz se construyen en el lifespan y se inyectan (spec 2026-10-20, fase 1)."""

import httpx
import pytest

from src.core.config import settings
from src.services.reconnection import ConnectionState
from src.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider


def test_app_state_has_stt_and_tts_providers(client):
    state = client.app.state.speech
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
    stt = client.app.state.speech.stt

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


def _fake_builders(monkeypatch):
    from src.services.speech import factory
    from tests.unit.test_speech_registry import NamedStt, NamedTts

    monkeypatch.setitem(factory.PROVIDER_BUILDERS["stt"], "fake", lambda s: NamedStt("fake"))
    monkeypatch.setitem(factory.PROVIDER_BUILDERS["tts"], "fake", lambda s: NamedTts("fake"))


def test_admin_speech_requires_token(client):
    assert client.get("/admin/speech").status_code == 422
    assert client.put("/admin/speech", json={}).status_code == 422
    bad = {"X-Admin-Token": "wrong"}
    assert client.get("/admin/speech", headers=bad).status_code == 401
    assert client.put("/admin/speech", json={}, headers=bad).status_code == 401


def test_admin_speech_get_and_put_swap(client, monkeypatch):
    _fake_builders(monkeypatch)
    h = {"X-Admin-Token": "test-admin-token"}
    body = client.get("/admin/speech", headers=h).json()
    assert body["stt"]["provider"] == "jota" and body["tts"]["provider"] == "jota"
    assert body["audio_gate"] == "off"
    r = client.put("/admin/speech", json={"tts_provider": "fake"}, headers=h)
    assert r.status_code == 200 and r.json() == {"stt": "jota", "tts": "fake"}
    assert client.get("/admin/speech", headers=h).json()["tts"]["provider"] == "fake"
    assert client.app.state.speech.tts.name == "fake"


def test_swap_rejected_without_key_returns_422_and_keeps_current(client):
    h = {"X-Admin-Token": "test-admin-token"}
    r = client.put("/admin/speech", json={"stt_provider": "elevenlabs"}, headers=h)
    assert r.status_code == 422
    assert "ELEVENLABS_API_KEY" in r.text
    assert client.get("/admin/speech", headers=h).json()["stt"]["provider"] == "jota"


def test_swap_with_failing_start_returns_503_naming_only_provider(client, monkeypatch):
    from src.services.speech import factory
    from tests.unit.test_speech_registry import FailingStartTts

    bad = FailingStartTts("bad")
    monkeypatch.setitem(factory.PROVIDER_BUILDERS["tts"], "bad", lambda s: bad)
    h = {"X-Admin-Token": "test-admin-token"}
    r = client.put("/admin/speech", json={"tts_provider": "bad"}, headers=h)
    assert r.status_code == 503
    assert "secret-detail" not in r.text and "bad" in r.text
    assert client.app.state.speech.tts.name == "jota"
    assert bad.closed == 1


def test_admin_tts_status_exposes_counters_only_for_providers_that_have_them(client, monkeypatch):
    from pydantic import SecretStr

    h = {"X-Admin-Token": "test-admin-token"}
    assert "counters" not in client.get("/admin/tts/status", headers=h).json()
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", SecretStr("k"))
    monkeypatch.setattr(settings, "ELEVENLABS_TTS_DEFAULT_VOICE", "gc61tTk93h3LBXPxew2V")
    monkeypatch.setattr(settings, "ELEVENLABS_BASE_URL", "ws://127.0.0.1:1")  # nada escucha
    r = client.put("/admin/speech", json={"tts_provider": "elevenlabs"}, headers=h)
    assert r.status_code == 200
    body = client.get("/admin/tts/status", headers=h).json()
    assert body["provider"] == "elevenlabs"
    assert body["counters"] == {"capacity_refusals": 0, "provider_429": 0, "auth_errors": 0}


def test_admin_transcriber_status_exposes_counters_only_for_providers_that_have_them(
    client, monkeypatch
):
    from pydantic import SecretStr

    h = {"X-Admin-Token": "test-admin-token"}
    assert "counters" not in client.get("/admin/transcriber/status", headers=h).json()
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", SecretStr("k"))
    monkeypatch.setattr(settings, "ELEVENLABS_BASE_URL", "ws://127.0.0.1:1")  # nada escucha
    r = client.put("/admin/speech", json={"stt_provider": "elevenlabs"}, headers=h)
    assert r.status_code == 200
    body = client.get("/admin/transcriber/status", headers=h).json()
    assert body["provider"] == "elevenlabs"
    assert body["counters"] == {
        "capacity_refusals": 0,
        "provider_429": 0,
        "auth_errors": 0,
        "upstream_opens": 0,
        "audio_seconds_sent": 0.0,
    }
