"""Tests para GET /api/health."""
import httpx


def test_health_all_ok(client):
    """Todos los servicios responden → todos los campos son 'ok'."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["orchestrator"] == "ok"
    assert body["transcriber"] == "ok"
    assert body["tts"] == "ok"


def test_health_never_returns_5xx(client, mock_orchestrator):
    """Aunque el orchestrator esté caído, health devuelve 200."""
    from unittest.mock import AsyncMock
    mock_orchestrator.ping = AsyncMock(return_value=False)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["orchestrator"] == "unavailable"


def test_health_partial_outage_tts(client, mock_services):
    """TTS caído → campo tts es 'unavailable', el resto 'ok'."""
    mock_services.get("http://localhost:8005/health").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["tts"] == "unavailable"
    assert body["orchestrator"] == "ok"
    assert body["transcriber"] == "ok"
