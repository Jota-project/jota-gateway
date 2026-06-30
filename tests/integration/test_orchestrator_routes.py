"""Integration tests for /admin/orchestrators/{name}/status and /reconnect."""
from unittest.mock import AsyncMock


def test_get_orchestrator_status_connected(client, admin_headers):
    response = client.get("/admin/orchestrators/openclaw/status", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "openclaw"
    assert data["state"] == "CONNECTED"
    assert data["reconnect_attempts"] == 0
    assert data["last_error"] is None


def test_get_orchestrator_status_not_found(client, admin_headers):
    response = client.get("/admin/orchestrators/unknown/status", headers=admin_headers)
    assert response.status_code == 404


def test_get_orchestrator_status_requires_admin_token(client):
    response = client.get("/admin/orchestrators/openclaw/status")
    assert response.status_code == 422


def test_post_orchestrator_reconnect_accepted(client, admin_headers):
    response = client.post("/admin/orchestrators/openclaw/reconnect", headers=admin_headers)
    assert response.status_code == 202


def test_post_orchestrator_reconnect_not_found(client, admin_headers):
    response = client.post("/admin/orchestrators/unknown/reconnect", headers=admin_headers)
    assert response.status_code == 404
