"""Integration tests for /admin/transcriber/status and /admin/tts/status."""
from unittest.mock import patch


def test_get_transcriber_status_reachable(client, admin_headers):
    with patch("src.services.transcriber_client.TranscriberClient.ping", return_value=True):
        response = client.get("/admin/transcriber/status", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "transcriber"
    assert data["state"] == "CONNECTED"


def test_get_transcriber_status_unreachable(client, admin_headers):
    with patch("src.services.transcriber_client.TranscriberClient.ping", return_value=False):
        response = client.get("/admin/transcriber/status", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "DEGRADED"


def test_get_transcriber_status_requires_admin_token(client):
    response = client.get("/admin/transcriber/status")
    assert response.status_code == 422


def test_get_tts_status_default_connected(client, admin_headers):
    response = client.get("/admin/tts/status", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "tts"
    assert data["state"] == "CONNECTED"
    assert data["reconnect_attempts"] == 0


def test_get_tts_status_requires_admin_token(client):
    response = client.get("/admin/tts/status")
    assert response.status_code == 422
