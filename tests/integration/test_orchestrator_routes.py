"""Integration tests for /admin/orchestrators/{name}/status and /reconnect."""


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


def test_post_orchestrator_reconnect_returns_job_id_without_blocking_connect(
    client, admin_headers, mock_registry
):
    """Issue #103: the endpoint must coalesce with the background reconnect
    loop via trigger_reconnect() and return a job id — not call the blocking
    connect() directly (which used to race the background reconnect loop)."""
    connect_calls_before = mock_registry.connect.call_count

    response = client.post("/admin/orchestrators/openclaw/reconnect", headers=admin_headers)

    assert response.status_code == 202
    data = response.json()
    assert data["accepted"] is True
    assert isinstance(data["job_id"], str) and data["job_id"]
    mock_registry.trigger_reconnect.assert_called_once()
    assert mock_registry.connect.call_count == connect_calls_before


def test_post_orchestrator_reconnect_not_found(client, admin_headers):
    response = client.post("/admin/orchestrators/unknown/reconnect", headers=admin_headers)
    assert response.status_code == 404
