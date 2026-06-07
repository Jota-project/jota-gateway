"""Tests para GET /api/models."""
import httpx
from tests.integration.conftest import DB_BASE


def test_get_models_returns_list(client, auth_headers):
    r = client.get("/api/models", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["id"] == "llama3"


def test_get_models_db_unavailable_returns_503(client, auth_headers, mock_services):
    """Error de conexión a jota-db → 503."""
    mock_services.get(f"{DB_BASE}/models").mock(
        side_effect=httpx.ConnectError("db down")
    )
    r = client.get("/api/models", headers=auth_headers)
    assert r.status_code == 503
