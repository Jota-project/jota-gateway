"""Tests for GET /healthz and GET /ready."""

from unittest.mock import AsyncMock

import httpx

from src.core.config import settings


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
