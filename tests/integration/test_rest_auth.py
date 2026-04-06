"""Tests para validación de API key (deps.py → db_client.get_session)."""


def test_missing_api_key_returns_422(client):
    """Sin X-API-Key header → FastAPI devuelve 422 (header requerido ausente)."""
    r = client.get("/api/config")
    assert r.status_code == 422


def test_invalid_api_key_returns_401(client):
    """Key inválida → db devuelve 401 → gateway devuelve 401."""
    r = client.get("/api/config", headers={"x-api-key": "wrong-key"})
    assert r.status_code == 401


def test_valid_api_key_passes(client, auth_headers):
    """Key válida → resolución correcta, request pasa."""
    r = client.get("/api/config", headers=auth_headers)
    assert r.status_code == 200
