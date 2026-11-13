"""Integration tests for /admin/orchestrators/{name}/status and /reconnect."""


def test_get_orchestrator_status_connected(client, admin_headers):
    response = client.get("/admin/orchestrator/status", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "openclaw"
    assert data["state"] == "CONNECTED"
    assert data["reconnect_attempts"] == 0
    assert data["last_error"] is None


def test_old_named_routes_are_gone(client, admin_headers):
    r = client.get("/admin/orchestrators/openclaw/status", headers=admin_headers)
    assert r.status_code == 404
    r = client.post("/admin/orchestrators/openclaw/reconnect", headers=admin_headers)
    assert r.status_code in (404, 405)


def test_get_orchestrator_status_requires_admin_token(client):
    response = client.get("/admin/orchestrator/status")
    assert response.status_code == 422


def test_post_orchestrator_reconnect_accepted(client, admin_headers):
    response = client.post("/admin/orchestrator/reconnect", headers=admin_headers)
    assert response.status_code == 202


def test_post_orchestrator_reconnect_triggers_without_blocking_connect(
    client, admin_headers, mock_registry
):
    """Issue #103: the endpoint must call trigger_reconnect() (the loop retries now) and
    not the blocking connect() directly. #272: no job id any more."""
    connect_calls_before = mock_registry.connect.call_count

    response = client.post("/admin/orchestrator/reconnect", headers=admin_headers)

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    mock_registry.trigger_reconnect.assert_called_once()
    assert mock_registry.connect.call_count == connect_calls_before
