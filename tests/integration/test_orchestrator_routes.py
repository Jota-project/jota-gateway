"""
Integration tests for GET /api/orchestrators/{name}/status
and POST /api/orchestrators/{name}/reconnect.

Uses the `client` fixture (mock registry + mock jota-db) from conftest.py.
"""


def test_get_orchestrator_status_connected(client, auth_headers):
    response = client.get("/api/orchestrators/openclaw/status", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "openclaw"
    assert data["state"] == "CONNECTED"
    assert data["reconnect_attempts"] == 0
    assert data["last_error"] is None


def test_get_orchestrator_status_not_found(client, auth_headers):
    response = client.get("/api/orchestrators/unknown/status", headers=auth_headers)

    assert response.status_code == 404


def test_post_orchestrator_reconnect_accepted(client, auth_headers):
    response = client.post("/api/orchestrators/openclaw/reconnect", headers=auth_headers)

    assert response.status_code == 202


def test_post_orchestrator_reconnect_not_found(client, auth_headers):
    response = client.post("/api/orchestrators/unknown/reconnect", headers=auth_headers)

    assert response.status_code == 404
