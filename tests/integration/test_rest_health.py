"""Tests for GET /healthz and GET /ready."""

from unittest.mock import AsyncMock

import httpx
import pytest

from jota_gateway.core.config import settings


def test_healthz_always_200(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_all_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["services"]["orchestrator"] == "ok"
    assert body["services"]["transcriber"] == "ok"
    assert body["services"]["tts"] == "ok"


def test_ready_orchestrator_down_returns_503(client, mock_orchestrator):
    mock_orchestrator.ping = AsyncMock(return_value=False)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"
    assert r.json()["services"]["orchestrator"] == "unavailable"


def test_ready_tts_down_returns_200_degraded(client, mock_services):
    mock_services.get("http://localhost:8005/ready").mock(side_effect=httpx.ConnectError("down"))
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["services"]["tts"] == "unavailable"
    assert body["services"]["orchestrator"] == "ok"


def test_ready_transcriber_down_returns_200_degraded(client, mock_services):
    mock_services.get(f"http://{settings.TRANSCRIBER_WS_URL}/ready").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["services"]["transcriber"] == "unavailable"


def test_lifespan_runs_migrations(client, _skip_lifespan_migrations):
    # The fixture stubs run_migrations out; this pins that startup still calls it.
    assert _skip_lifespan_migrations == [None]


# --- voz `off`: un proveedor desactivado a propósito no degrada /ready ----------------------


@pytest.fixture
def stt_off(monkeypatch):
    """Antes que `client`, que construye los proveedores al arrancar."""
    monkeypatch.setattr(settings, "STT_PROVIDER", "off")


@pytest.fixture
def tts_off(monkeypatch):
    monkeypatch.setattr(settings, "TTS_PROVIDER", "off")


def test_ready_voice_off_is_ok_not_degraded(stt_off, tts_off, client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "services": {
        "orchestrator": "ok", "transcriber": "unavailable", "tts": "unavailable"}}


def test_ready_stt_off_and_tts_down_is_degraded(stt_off, client, mock_services):
    mock_services.get("http://localhost:8005/ready").mock(side_effect=httpx.ConnectError("down"))
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["services"]["tts"] == "unavailable"


def test_ready_voice_off_and_orchestrator_down_is_503(stt_off, tts_off, client,
                                                       mock_orchestrator):
    mock_orchestrator.ping = AsyncMock(return_value=False)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"
